"""Pin a verified Python package so a running tick cannot mix worktree versions."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile


def _digest(files):
    digest=hashlib.sha256()
    for name,raw in files.items():
        digest.update(str(name).encode()+b'\0'+raw+b'\0')
    return digest.hexdigest()


def package_digest(package):
    package=Path(package).resolve()
    files={p.relative_to(package):p.read_bytes() for p in sorted(package.rglob('*.py'))}
    if Path('__init__.py') not in files:
        raise ValueError('Runner package is incomplete')
    return _digest(files)


def pin_release(package, directory):
    package=Path(package).resolve()
    directory=Path(directory).resolve()
    files={p.relative_to(package):p.read_bytes() for p in sorted(package.rglob('*.py'))}
    if Path('__init__.py') not in files:
        raise ValueError('Runner package is incomplete')
    target=directory/_digest(files)
    directory.mkdir(parents=True,exist_ok=True)
    if target.exists():
        existing={p.relative_to(target/'nba_forecast'):p.read_bytes() for p in (target/'nba_forecast').rglob('*.py')}
        if existing!=files:
            raise ValueError('Pinned runner release failed integrity verification')
        return target
    stage=Path(tempfile.mkdtemp(prefix='.stage-',dir=directory))
    try:
        for name,raw in files.items():
            p=stage/'nba_forecast'/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(raw)
        if any((package/name).read_bytes()!=raw for name,raw in files.items()):
            raise ValueError('Runner source changed while pinning; retry after editing finishes')
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    args=parser.parse_args()
    print(pin_release(Path(__file__).parent,args.directory))
