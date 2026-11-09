"""History preparation done once per input state, not once per minute.

The tick runs every minute in a new process. Its history phase re-normalized
every archived LiveData box score, re-read and validated every captured final
and the whole input history, and the player sync then did all of it a second
time. None of it changes unless a final, a box-score archive or the input
history changes. Each step's inputs are reduced here to a key of file names,
sizes and modification times plus the package's source digest, and a step is
skipped when its key equals the one recorded after it last ran:

- ``enrich_final_cache`` is a fixpoint (run on its own output it changes
  nothing), so its key is taken after it runs and a later tick with the same
  finals and box scores skips it, re-reporting the archives it skipped;
- the merged history is stored under its key (``cache/history/``) and read back;
- the input history's validation is recorded once per key.

Within a tick the result is shared: a second call (the player sync's) costs a
stat of the finals unless a final was captured in between, in which case the
steps run again exactly as before. A missing, unreadable or differently keyed
record means the step runs. ``HISTORY_RECORD`` holds the keys; deleting
``cache/`` only costs one tick the old work.
"""
import hashlib
import json
import os
from pathlib import Path
import pickle

from .files import atomic_bytes
from .replay_cache import code_digest
from .schedule import canonical

VERSION = 1
HISTORY_RECORD = 'cache/history-memo.json'
# ``enrich_archive`` reads these and only these provenance records.
BOXSCORE_URL = '/liveData/boxscore/boxscore_002'


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _stat(path):
    try:
        info = os.stat(path)
        return [info.st_size, info.st_mtime_ns]
    except OSError:
        return None


def history_key(paths):
    """The input history files by name, size and modification time; ``None`` when any cannot be read."""
    stamps = []
    for path in paths or []:
        stamp = _stat(path)
        if stamp is None:
            return None
        stamps.append([str(Path(path).resolve()), *stamp])
    return digest(['files', stamps])


def _read_record(path):
    try:
        record = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(record, dict) or record.get('version') != VERSION or record.get('code') != code_digest():
        # Another release's keys and archive classification are not trusted.
        return {}
    return record


_MISSING = object()


def _horizon(rows):
    """The latest game or observation time among ``rows``, as stored text; ``None`` for none."""
    from .validation import parse_time
    latest = max((t for r in rows for t in (r['game_time'], r['known_at'])), key=parse_time, default=None)
    return latest


class HistoryMemo:
    """The tick's merged history (input history plus captured finals, enriched from box scores)."""

    def __init__(self, folder, history, key=None):
        self.folder = Path(folder)
        self.history = history
        # Without file stamps the key is the content digest of the input rows.
        self.key = key or digest(['rows', history])
        self.path = self.folder/HISTORY_RECORD
        self.record = _read_record(self.path)
        self.saved = canonical(self.record)
        self.current = None
        self.merged_horizon = None

    def horizon(self):
        """The latest game or observation time in the last merged history (``merged()`` first)."""
        return self.merged_horizon

    def _save(self):
        self.record.update(version=VERSION, code=code_digest())
        encoded = canonical(self.record)
        if encoded != self.saved:
            atomic_bytes(self.path, encoded)
            self.saved = encoded

    def validate_input(self):
        """``validate_history`` of the input history, once per input key."""
        from .validation import validate_history
        if self.record.get('validated') == self.key:
            return
        validate_history(self.history)
        self.record['validated'] = self.key
        self._save()

    def _finals(self):
        folder = self.folder/'finals'
        return sorted([p.name, *(_stat(p) or [None, None])] for p in folder.glob('*.json')) if folder.exists() else []

    def _boxscores(self):
        """The LiveData box-score archives ``enrich_archive`` reads, by provenance name, with both files' stamps.

        A provenance record is written once per content digest, so each is read
        once to classify it and afterwards only listed: ``None`` is a record
        enrichment ignores, a digest a box score, ``''`` a record it cannot use
        (it reports those, so they stay in the key).
        """
        raw = self.folder/'raw'
        known = self.record.setdefault('provenance', {})
        names = sorted(p.name for p in raw.glob('*.provenance.json')) if raw.exists() else []
        current = set(names)
        for name in [n for n in known if n not in current]:
            del known[name]
        stamps = []
        for name in names:
            if name not in known:
                try:
                    meta = json.loads((raw/name).read_text())
                except (OSError, ValueError):
                    # Unreadable: enrichment reports it; read again next time.
                    stamps.append([name, _stat(raw/name)])
                    continue
                url = meta.get('source_url', '') if isinstance(meta, dict) else None
                if not isinstance(url, str):
                    known[name] = ''
                elif BOXSCORE_URL not in url:
                    known[name] = None
                else:
                    sha = meta.get('sha256')
                    known[name] = sha if isinstance(sha, str) and sha else ''
            sha = known[name]
            if sha is not None:
                stamps.append([name, _stat(raw/name), _stat(raw/(sha+'.json')) if sha else None])
        return stamps

    def merged(self):
        """``(history, enrich_skipped, merge_errors, key, failure)`` as enrichment and ``_merge_finals`` would produce now.

        ``enrich_skipped`` is what ``enrich_final_cache(skipped=[])`` returns and
        ``merge_errors`` what ``_merge_finals`` appends to its ``errors``; ``key``
        names the merged history's inputs. An unexpected enrichment exception is
        returned as ``failure`` (the caller reports it as before), the merge of
        the finals as they are still returned, and nothing about it is kept, so
        the next call runs enrichment again. A merge failure raises, unrecorded.
        """
        finals = self._finals()
        boxes = self._boxscores()
        if self.current and self.current[0] == digest(['enrich', finals, boxes]):
            return self.current[1]
        enrichment = self.record.get('enrich') or {}
        failure = None
        if enrichment.get('key') == digest(['enrich', finals, boxes]):
            skipped = list(enrichment.get('skipped', []))
        else:
            from .player_sync import enrich_final_cache
            try:
                skipped = enrich_final_cache(self.folder, skipped=[])
            except Exception as exc:
                failure, skipped = exc, []
            finals = self._finals()
            if failure is None:
                self.record['enrich'] = dict(key=digest(['enrich', finals, boxes]), skipped=skipped)
            else:
                self.record.pop('enrich', None)
        merge_key = digest(['merge', self.key, finals])
        merge = self.record.get('merge') or {}
        store = self.folder/'cache'/'history'
        value = None
        if merge.get('key') == merge_key:
            try:
                value = pickle.loads((store/(merge_key+'.pickle')).read_bytes())
            except Exception:
                value = None
            if not isinstance(value, list):
                value = None
        if value is not None:
            errors = list(merge.get('errors', []))
            horizon = merge.get('horizon', _MISSING)
            if horizon is _MISSING:
                horizon = _horizon(value)
        else:
            from .operations import _merge_finals
            errors = []
            value = _merge_finals(self.history, self.folder/'finals', errors=errors)
            horizon = _horizon(value)
            try:
                atomic_bytes(store/(merge_key+'.pickle'), pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
                for old in store.glob('*.pickle'):
                    if old.name != merge_key+'.pickle':
                        old.unlink()
                self.record['merge'] = dict(key=merge_key, errors=errors, horizon=horizon)
            except OSError:
                self.record.pop('merge', None)
        self._save()
        self.merged_horizon = horizon
        result = (value, list(skipped), list(errors), merge_key, failure)
        self.current = (digest(['enrich', finals, boxes]), result) if failure is None else None
        return result
