"""The test suite is offline: any connection that leaves this machine fails at once.

Until 2026-11-21 a tick run inside a test reached the real ESPN, Kalshi and NBA
hosts (about 190 requests a suite run). The tests passed either way while those
hosts answered as expected, and three failed on main when the NBA moved its
injury-report page (2026-11-20). A test that needs a source's answer patches the
fetch; one that does not sees the same refusal on every machine.
"""
import socket

LOOPBACK = ('127.0.0.1', 'localhost', '::1', '', None)
_connect, _getaddrinfo = socket.socket.connect, socket.getaddrinfo


class OutsideConnection(OSError):
    pass


def _refuse(target):
    raise OutsideConnection(f'tests are offline: patch the fetch that wanted {target!r}')


def _guarded_connect(self, address, *args, **kwargs):
    if isinstance(address, tuple) and address and address[0] not in LOOPBACK:
        _refuse(address[0])
    return _connect(self, address, *args, **kwargs)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if isinstance(host, bytes):
        host = host.decode('ascii', 'replace')
    if host not in LOOPBACK:
        _refuse(host)
    return _getaddrinfo(host, *args, **kwargs)


def install():
    socket.socket.connect, socket.getaddrinfo = _guarded_connect, _guarded_getaddrinfo
