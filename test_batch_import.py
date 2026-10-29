import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import fastdl


class BatchImportTests(unittest.TestCase):
    def setUp(self):
        fastdl.BATCH_IMPORTS.clear()

    def test_native_review_deduplicates_without_downloading(self):
        window = Mock()
        urls = ['https://example.com/a', 'https://example.com/a', 'http://example.com/b']
        with patch.object(fastdl, 'WINDOW', window), patch.object(fastdl, 'show_window') as show, patch.object(fastdl.M, 'add') as add:
            result = fastdl.batch_import(urls)
        self.assertEqual(result, {'ok': True, 'count': 2, 'window': 'app'})
        show.assert_called_once()
        script = window.evaluate_js.call_args.args[0]
        self.assertEqual(json.loads(script[len('openMulti('):-1]), 'https://example.com/a\nhttp://example.com/b')
        add.assert_not_called()

    def test_all_validation_precedes_ui(self):
        for value in (None, [], ['javascript:alert(1)'], ['https://good.test/a', 'bad'], ['https://a.test/\nx'], ['https://a.test:bad'], ['https://a.test'] * 501):
            with self.subTest(value=str(value)[:60]), patch.object(fastdl, 'show_window') as show, patch.object(fastdl.webbrowser, 'open') as browser:
                with self.assertRaises(ValueError):
                    fastdl.batch_import(value)
                show.assert_not_called()
                browser.assert_not_called()

    def test_browser_handoff_is_consumed_once_and_requires_trusted_post(self):
        server = fastdl.Server(('127.0.0.1', 0), fastdl.API)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(fastdl, 'PORT', server.server_port), patch.object(fastdl, 'WINDOW', None), patch.object(fastdl.webbrowser, 'open') as browser, patch.object(fastdl.M, 'add') as add:
                data = json.dumps({'urls': ['https://example.com/a']}).encode()
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(urllib.request.Request(base + '/api/batch', data=data))
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                req = urllib.request.Request(base + '/api/batch', data=data, headers={'X-FastDL': '1'})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(json.load(response)['window'], 'browser')
                token = browser.call_args.args[0].split('batch=')[1]
                with urllib.request.urlopen(base + '/api/batch/' + token) as response:
                    self.assertEqual(json.load(response)['urls'], ['https://example.com/a'])
                with self.assertRaises(urllib.error.HTTPError) as consumed:
                    urllib.request.urlopen(base + '/api/batch/' + token)
                self.assertEqual(consumed.exception.code, 404)
                consumed.exception.close()
                add.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()

    def test_expiry_and_capacity(self):
        with patch.object(fastdl, 'WINDOW', None), patch.object(fastdl.webbrowser, 'open'), patch.object(fastdl.time, 'monotonic', return_value=1000):
            fastdl.BATCH_IMPORTS['old'] = (0, ['https://example.com'])
            for i in range(25):
                fastdl.batch_import([f'https://example.com/{i}'])
        self.assertNotIn('old', fastdl.BATCH_IMPORTS)
        self.assertEqual(len(fastdl.BATCH_IMPORTS), 20)


if __name__ == '__main__':
    unittest.main()
