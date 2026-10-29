"""Quality lookup regression tests without contacting video sites."""
import unittest
from unittest.mock import patch

import fastdl


class QualityLookupTests(unittest.TestCase):
    def test_youtube_qualities_without_playback(self):
        metadata = {"title": "Paused video", "formats": [
            {"format_id": "22", "height": 720, "vcodec": "avc1", "acodec": "aac",
             "ext": "mp4", "protocol": "https"},
            {"format_id": "18", "height": 360, "vcodec": "avc1", "acodec": "aac",
             "ext": "mp4", "protocol": "https"},
        ]}
        with patch('yt_dlp.YoutubeDL') as factory, patch.object(fastdl, 'ytdlp_auth', return_value={}):
            extractor = factory.return_value.__enter__.return_value
            extractor.extract_info.return_value = metadata
            menu = fastdl.formats('https://www.youtube.com/watch?v=example', streams=[])
            self.assertEqual(len(menu['items']), 2)
            self.assertIn('720p', menu['items'][0]['label'])
            options = factory.call_args.args[0]
            self.assertEqual(options['extractor_retries'], 0)
            self.assertEqual(options['retries'], 0)
            self.assertLessEqual(options['socket_timeout'], 8)
            extractor.extract_info.assert_called_once_with(
                'https://www.youtube.com/watch?v=example', download=False)

    def test_lookup_error_still_offers_download(self):
        with patch('yt_dlp.YoutubeDL') as factory, patch.object(fastdl, 'ytdlp_auth', return_value={}):
            factory.return_value.__enter__.return_value.extract_info.side_effect = TimeoutError('timed out')
            menu = fastdl.formats('https://www.youtube.com/watch?v=example')
            self.assertTrue(menu['error'])
            self.assertEqual(menu['items'][0]['label'], 'Best quality')


if __name__ == '__main__':
    unittest.main()
