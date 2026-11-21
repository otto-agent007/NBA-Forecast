"""Installs the offline guard for a discovered run and checks that it holds."""
import socket
import unittest
import urllib.request

try:
    from tests import offline
except ModuleNotFoundError:  # discovered with the tests folder as the top level
    import offline

offline.install()


class OfflineGuardTests(unittest.TestCase):
    def test_an_outside_request_is_refused_without_leaving_the_machine(self):
        with self.assertRaises(OSError) as caught:
            urllib.request.urlopen('https://www.nba.com/players/injury-report', timeout=5)
        self.assertIn('tests are offline', str(caught.exception))
        with self.assertRaises(offline.OutsideConnection):
            socket.create_connection(('192.0.2.1', 443), timeout=1)

    def test_loopback_stays_open(self):
        server = socket.socket()
        self.addCleanup(server.close)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()


if __name__ == '__main__':
    unittest.main()
