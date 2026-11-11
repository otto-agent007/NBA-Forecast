"""Shared helpers for the tick-memo verification scripts: block the network, load the production history file."""
import socket, urllib.request, gzip, json
from pathlib import Path

HISTORY = str(Path(__file__).resolve().parents[2] / 'datasets' / 'regular-season-expanded-2026-10-24-home' / 'history.json.gz')

def block_network():
    def refuse(*a, **k):
        raise OSError('network disabled for offline verification')
    socket.create_connection = refuse
    urllib.request.urlopen = refuse
    try:
        import http.client
        http.client.HTTPConnection.connect = lambda self: refuse()
    except Exception:
        pass

def load_history(path=HISTORY):
    with gzip.open(path, 'rt') as f:
        return json.load(f)
