"""Synthetic-only tests: real account IDs, captions and insights never belong in git."""
import importlib.util
import json
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / 'scripts' / 'import_zoho_threads.py'
spec = importlib.util.spec_from_file_location('import_zoho_threads', SRC)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

CAPTURED_AT = '2026-11-20T01:50:00+08:00'


def sample(post_id='12345678901234567', handle='example_personal', **kwargs):
    p = {'id': post_id, 'from': {'name': handle}, 'created_time': 1795000000000,
         'perma_link': f'https://www.threads.com/@{handle}/post/example',
         'message': '圖表裡的每條線原本都一樣黑。', 'view_count': 1234,
         'like_count': 42, 'comment_count': 3, 'retweet_count': 2,
         'attachments': {'data': [{'media_type': 'video'}]}}
    p.update(kwargs)
    return p


class BridgeTests(unittest.TestCase):
    def bundles(self, data):
        return bridge.make_bundles(data, 'example_personal', CAPTURED_AT)

    def test_native_and_mcp_envelopes(self):
        for data in ([sample()], {'data': [sample()]},
                     {'status': 'success', 'data': {'cursor': None, 'data': [sample()]}},
                     {'text': json.dumps({'data': {'data': [sample()]}})}):
            with self.subTest(data=type(data).__name__):
                self.assertEqual(len(self.bundles(data)), 1)

    def test_strict_account_isolation(self):
        for handle in ('brand_account', 'example-personal', ''):
            with self.subTest(handle=handle), self.assertRaises(ValueError):
                self.bundles([sample(handle=handle)])

    def test_mixed_account_aborts_entire_batch(self):
        with self.assertRaisesRegex(ValueError, 'account mismatch'):
            self.bundles([sample(), sample('12345678901234568', handle='brand_account')])

    def test_missing_is_null_not_zero(self):
        data = self.bundles([sample(comment_count=None, retweet_count=None)])[0]
        metrics = data['snapshot']['metrics']
        self.assertEqual(metrics['views'], 1234)
        self.assertEqual(metrics['likes'], 42)
        self.assertIsNone(metrics['comments'])
        self.assertIsNone(metrics['reposts'])
        self.assertIsNone(metrics['shares'])
        self.assertIsNone(metrics['quotes'])
        self.assertNotIn('plays', metrics)

    def test_no_source_share_substitution(self):
        self.assertEqual(self.bundles([sample()])[0]['snapshot']['metrics']['reposts'], 2)
        self.assertIsNone(self.bundles([sample()])[0]['snapshot']['metrics']['shares'])

    def test_ids_and_timezone(self):
        data = self.bundles([sample()])[0]
        self.assertEqual(data['post']['post_id'], 'threads:example_personal:12345678901234567')
        self.assertTrue(data['post']['published_at'].endswith('+00:00'))
        self.assertTrue(data['snapshot']['captured_at'].endswith('+00:00'))
        self.assertEqual(data['snapshot']['platform_scope'], 'threads')
        self.assertEqual(data['post']['platforms'], ['threads'])

    def test_duplicate_post_id(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.bundles([sample(), sample()])

    def test_missing_captured_timezone(self):
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            bridge.make_bundles([sample()], 'example_personal', '2026-11-20T12:00:00')

    def test_capture_precedes_publication(self):
        with self.assertRaisesRegex(ValueError, 'predates publication'):
            bridge.make_bundles([sample()], 'example_personal', '2026-10-01T00:00:00Z')

    def test_wrong_permalink(self):
        with self.assertRaisesRegex(ValueError, 'permalink'):
            self.bundles([sample(perma_link='https://www.threads.com/@brand_account/post/a')])

    def test_invalid_counters(self):
        for val in (-1, True, '123', 1.5):
            with self.subTest(val=val), self.assertRaises(ValueError):
                self.bundles([sample(view_count=val)])

    def test_error_and_empty_page_not_success(self):
        for data in ({'status': 'failure', 'message': 'bad token'}, {'data': []},
                     {'data': {'message': 'error'}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.bundles(data)

    def test_content_type(self):
        self.assertEqual(self.bundles([sample()])[0]['post']['content_type'], 'short_video')
        self.assertEqual(self.bundles([sample(attachments={'data':[{'media_type':'image'}]})])[0]['post']['content_type'], 'image')


if __name__ == '__main__':
    unittest.main()
