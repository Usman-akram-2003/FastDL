"""Local-server checks for range response integrity; python -m unittest test_range_responses."""
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import fastdl


DATA = b'0123456789abcdef'


class RangeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        a, b = map(int, self.headers['Range'][6:].split('-'))
        body = DATA[a:b + 1]
        value = f'bytes {a}-{b}/{len(DATA)}'
        if self.path == '/offset':
            value = f'bytes 0-{b}/{len(DATA)}'
        elif self.path == '/total':
            value = f'bytes {a}-{b}/{len(DATA) + 1}'
        elif self.path == '/end':
            value = f'bytes {a}-{b + 1}/{len(DATA)}'
        elif self.path == '/malformed':
            value = 'invalid'
        elif self.path == '/truncated':
            body = b''
        self.send_response(206)
        if self.path != '/missing':
            self.send_header('Content-Range', value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class RangeResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), RangeHandler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def worker(self, path):
        url = f'http://127.0.0.1:{self.server.server_port}/{path}'
        download = fastdl.Download(url)
        download.size = len(DATA)
        segment = [4, 11]
        with tempfile.TemporaryDirectory() as folder:
            part = os.path.join(folder, 'part')
            with open(part, 'wb') as f:
                f.write(b'_' * len(DATA))
            with patch.object(download.stop, 'wait', return_value=False), patch.object(download, '_push_back', return_value=False):
                download._worker([url], part, segment)
            with open(part, 'rb') as f:
                contents = f.read()
        return download, segment, contents

    def test_valid_response_writes_requested_bytes(self):
        download, segment, contents = self.worker('valid')
        self.assertEqual(segment, [12, 11])
        self.assertFalse(download.errors)
        self.assertEqual(contents, b'____' + DATA[4:12] + b'____')

    def test_invalid_responses_preserve_partial_file(self):
        for path in ('offset', 'total', 'end', 'malformed', 'missing', 'truncated'):
            with self.subTest(path=path):
                download, segment, contents = self.worker(path)
                self.assertTrue(download.errors)
                self.assertEqual(segment, [4, 11])
                self.assertEqual(contents, b'_' * len(DATA))


if __name__ == '__main__':
    unittest.main()
