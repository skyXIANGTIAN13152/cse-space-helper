"""Version and trust checks for the public release checker; no live network."""
import io
import json
import queue
import unittest
from unittest.mock import Mock
import urllib.error

import updater


def release(tag='v1.2.0'):
    return {'tag_name': tag, 'draft': False, 'prerelease': False,
            'html_url': updater.RELEASE_ROOT+'tag/'+tag,
            'assets': [{'name': 'CSE-Space-Helper-'+lang+'.exe', 'size': 20000,
                        'digest': 'sha256:'+'a'*64,
                        'state': 'uploaded', 'browser_download_url': updater.RELEASE_ROOT+
                        'download/'+tag+'/CSE-Space-Helper-'+lang+'.exe'} for lang in ['zh', 'en']]}


class Response(io.BytesIO):
    def geturl(self):
        return updater.API_URL


class UpdateTests(unittest.TestCase):
    def test_semantic_versions_not_lexical_order(self):
        self.assertTrue(updater.release_update(release('v1.10.0'), '1.9.0')['available'])
        self.assertFalse(updater.release_update(release('v1.9.0'), '1.10.0')['available'])
        self.assertFalse(updater.release_update(release(), '1.2.0')['available'])

    def test_both_language_downloads_selected_correctly(self):
        for language in ['en', 'zh']:
            result = updater.release_update(release(), '1.0.0', language)
            self.assertTrue(result['available'])
            self.assertTrue(result['download_url'].endswith('-'+language+'.exe'))

    def test_drafts_and_prereleases_are_ignored(self):
        for key in ['draft', 'prerelease']:
            data = release()
            data[key] = True
            self.assertFalse(updater.release_update(data, '1.0.0')['available'])

    def test_malformed_and_injected_versions_rejected(self):
        for value in [None, 'v1.0.0-beta', '1.00.0', '1.2', '../1.2.0', 'v1.2.0?x=y', '1.2.'+'9'*100]:
            with self.subTest(value=value), self.assertRaises(updater.UpdateError):
                updater.version_tuple(value)

    def test_untrusted_release_page_rejected(self):
        for url in ['http://github.com/x', 'https://github.com/other/repo/releases/tag/v1.2.0',
                    'https://github.com.evil.test/', 'javascript:alert(1)']:
            data = release()
            data['html_url'] = url
            with self.subTest(url=url), self.assertRaises(updater.UpdateError):
                updater.release_update(data, '1.0.0')

    def test_missing_duplicate_or_unready_download_rejected(self):
        for mutation in ['missing', 'duplicate', 'uploading', 'size', 'url']:
            data = release()
            if mutation == 'missing': data['assets'] = []
            if mutation == 'duplicate': data['assets'].append(data['assets'][0])
            if mutation == 'uploading': data['assets'][0]['state'] = 'new'
            if mutation == 'size': data['assets'][0]['size'] = 0
            if mutation == 'url': data['assets'][0]['browser_download_url'] = 'https://example.com/app.exe'
            with self.subTest(mutation=mutation), self.assertRaises(updater.UpdateError):
                updater.release_update(data, '1.0.0', 'zh')

    def test_request_has_no_account_authentication_or_report_data(self):
        def opener(request, timeout):
            self.assertEqual(request.full_url, updater.API_URL)
            self.assertEqual(timeout, 8)
            self.assertIsNone(request.data)
            self.assertIsNone(request.get_header('Authorization'))
            self.assertNotIn('z1234567', str(request.headers))
            return Response(json.dumps(release()).encode())
        self.assertTrue(updater.check_release('1.0.0', 'en', opener=opener)['available'])

    def test_timeout_rate_limit_bad_json_and_oversized_response(self):
        cases = [TimeoutError(), urllib.error.HTTPError(updater.API_URL, 429, '', {}, None),
                 Response(b'not json'), Response(b'x'*(updater.MAX_RESPONSE+1))]
        for case in cases:
            def opener(request, timeout):
                if isinstance(case, Exception): raise case
                return case
            with self.subTest(case=type(case).__name__), self.assertRaises(updater.UpdateError):
                updater.check_release('1.0.0', opener=opener)

    def test_redirect_to_other_service_is_rejected(self):
        response = Response(json.dumps(release()).encode())
        response.geturl = lambda: 'https://example.com/releases/latest'
        with self.assertRaises(updater.UpdateError):
            updater.check_release('1.0.0', opener=lambda request, timeout: response)

    def test_update_event_does_not_end_active_cleanup(self):
        from app import App
        app = Mock()
        app.events = queue.Queue()
        app.events.put(('update', {'available': False}, False))
        app.busy = True
        app.operation = 'clean'
        App.poll(app)
        self.assertTrue(app.busy)
        self.assertEqual(app.operation, 'clean')
        app.spinner.stop.assert_not_called()
        app.set_buttons.assert_not_called()
        app.finish_update.assert_called_once()


if __name__ == '__main__':
    unittest.main()
