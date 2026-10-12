"""Offline trust and real-file transaction tests, entirely in private temp dirs.

Private Python dependency injections replace network/child process boundaries;
there are no production flags to bypass authentication or launch checks.
"""
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

import updater


def pe_bytes(label=b'new'):
    content = bytearray(128)
    content[:2] = b'MZ'
    content[60:64] = (64).to_bytes(4, 'little')
    content[64:68] = b'PE\0\0'
    return bytes(content)+label


def update_info(content=None, version='9.0.0'):
    content = content or pe_bytes()
    name = 'CSE-Space-Helper-zh.exe'
    return {'available': True, 'version': version, 'asset_name': name,
            'download_url': updater.RELEASE_ROOT+'download/v'+version+'/'+name,
            'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}


class Response(io.BytesIO):
    def __init__(self, content, url, headers=None):
        super().__init__(content)
        self.url = url
        self.headers = headers or {}

    def geturl(self):
        return self.url


class Child:
    pid = os.getpid()

    def __init__(self, exited=False):
        self.exited = exited

    def poll(self):
        return 1 if self.exited else None


class DownloadTests(unittest.TestCase):
    def test_streamed_download_verifies_asset_and_reports_bounded_progress(self):
        content = pe_bytes()+b'payload'*50000
        info = update_info(content)
        calls = []

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            self.assertEqual(request.full_url, info['download_url'])
            self.assertIsNone(request.data)
            self.assertIsNone(request.get_header('Authorization'))
            self.assertNotIn('password', str(request.headers).lower())
            return Response(content, 'https://release-assets.githubusercontent.com/asset?token=public',
                            {'Content-Length': str(len(content))})

        path = updater.download_update(info, lambda current, total: calls.append((current, total)),
                                       _opener=opener)
        try:
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(calls[0], (0, len(content)))
            self.assertEqual(calls[-1], (len(content), len(content)))
            self.assertGreater(len(calls), 2)
            self.assertFalse(path.with_suffix('.part').exists())
        finally:
            updater.discard_download(path)
        self.assertFalse(path.parent.exists())

    def test_hash_length_and_pe_mismatch_remove_partial_staging(self):
        good = pe_bytes()
        cases = [(good+b'extra', update_info(good), {}),
                 (good[:-1], update_info(good), {}),
                 (good.replace(b'new', b'bad'), update_info(good), {}),
                 (b'<html>not executable</html>', update_info(b'<html>not executable</html>'), {}),
                 (good, update_info(good), {'Content-Length': str(len(good)+1)})]
        for content, info, headers in cases:
            with self.subTest(size=len(content)), tempfile.TemporaryDirectory() as directory:
                with patch.object(updater.tempfile, 'tempdir', directory):
                    with self.assertRaises(updater.UpdateError):
                        updater.download_update(info, _opener=lambda req, timeout: Response(
                            content, info['download_url'], headers))
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancellation_before_and_during_stream_removes_partial(self):
        content = pe_bytes()+b'x'*(updater.CHUNK_SIZE*2)
        info = update_info(content)
        for before in (False, True):
            event = threading.Event()
            if before:
                event.set()
            with self.subTest(before=before), tempfile.TemporaryDirectory() as directory:
                def progress(received, total):
                    if received:
                        event.set()
                with patch.object(updater.tempfile, 'tempdir', directory):
                    with self.assertRaises(updater.UpdateCancelled):
                        updater.download_update(info, progress, event,
                            _opener=lambda req, timeout: Response(content, info['download_url']))
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_untrusted_redirect_is_rejected_before_following(self):
        info = update_info()
        handler = updater._AssetRedirectHandler(info['download_url'])
        request = urllib.request.Request(info['download_url'])
        urls = ['http://release-assets.githubusercontent.com/payload',
                'https://release-assets.githubusercontent.com.evil.test/payload',
                'https://release-assets.githubusercontent.com:8443/payload',
                'https://user:password@release-assets.githubusercontent.com/payload',
                'https://github.com/other/repo/releases/download/v9/app.exe',
                'https://example.com/app.exe']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(updater.UpdateError):
                handler.redirect_request(request, None, 302, '', {}, url)
        self.assertIsNotNone(handler.redirect_request(request, None, 302, '', {},
            'https://release-assets.githubusercontent.com/asset?se=public'))

    def test_final_response_url_is_rechecked(self):
        info = update_info()
        with self.assertRaises(updater.UpdateError):
            updater.download_update(info, _opener=lambda req, timeout: Response(
                pe_bytes(), 'https://evil.test/app.exe'))

    def test_truncated_http_and_callback_errors_always_clean_staging(self):
        for mode in ('http', 'callback'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                info = update_info()
                response = Response(pe_bytes(), info['download_url'])
                def truncated(size):
                    raise http.client.IncompleteRead(b'partial', 100)
                def callback(current, total):
                    raise RuntimeError('test callback failed')
                if mode == 'http':
                    response.read = truncated
                with patch.object(updater.tempfile, 'tempdir', directory):
                    with self.assertRaises(updater.UpdateError if mode == 'http' else RuntimeError):
                        updater.download_update(info, callback if mode == 'callback' else None,
                                                _opener=lambda req, timeout: response)
                    self.assertEqual(list(Path(directory).iterdir()), [])

    def test_metadata_requires_bounded_size_and_github_sha256_digest(self):
        from test_updater import release
        for value in (None, '', 'md5:'+'a'*32, 'sha256:'+'z'*64):
            data = release('v9.0.0')
            data['assets'][0]['digest'] = value
            with self.subTest(digest=value), self.assertRaises(updater.UpdateError):
                updater.release_update(data, '1.0.0', 'zh')
        data = release('v9.0.0')
        data['assets'][0]['size'] = updater.MAX_ASSET_SIZE+1
        with self.assertRaises(updater.UpdateError):
            updater.release_update(data, '1.0.0', 'zh')


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.installed = Path(self.directory.name)/'installed'
        self.installed.mkdir()
        self.root = Path(tempfile.mkdtemp(prefix=updater.STAGE_PREFIX, dir=self.directory.name))
        self.target = (self.installed/'CSE 空间助手.exe').resolve()
        self.old = pe_bytes(b'old version')
        self.new = pe_bytes(b'new version')
        self.target.write_bytes(self.old)
        self.info = update_info(self.new, version=updater.VERSION)
        (self.root/self.info['asset_name']).write_bytes(self.new)
        self.data = {'token': 'a'*64, 'parent_pid': os.getpid(), 'target': str(self.target),
                     'original_sha256': hashlib.sha256(self.old).hexdigest(),
                     'original_version': '1.0.0', 'info': self.info}
        self.manifest = self.root/'update.json'
        updater._write_json(self.manifest, self.data)
        cleanup = patch.object(updater, '_schedule_cleanup')
        cleanup.start()
        self.addCleanup(cleanup.stop)

    def frozen(self, executable=None):
        return patch.multiple(updater.sys, frozen=True, executable=str(executable or self.target),
                              create=True)

    def state(self):
        return updater._read_json(self.root/'state.json')

    def test_actual_atomic_replacement_and_ui_readiness_remove_old_backup(self):
        calls = []
        def start(executable, arguments):
            calls.append((executable, arguments))
            self.assertEqual(executable.read_bytes(), self.new)
            self.assertEqual(arguments[0], '--update-result')
            self.assertEqual(len(arguments), 2)
            with self.frozen():
                result = updater.confirm_update_startup(arguments[1])
            self.assertEqual(result['status'], 'updated')
            return Child()
        result = updater._perform_update(self.manifest, _start=start,
                                        _wait=lambda pid: None, _ready_timeout=1)
        self.assertEqual(result, 'updated')
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual(self.state()['status'], 'updated')
        self.assertEqual(len(calls), 1)
        self.assertFalse(list(self.installed.glob('*.backup.exe')))
        self.assertFalse(list(self.installed.glob('*.new.exe')))

    def test_actual_rollback_when_new_process_cannot_start(self):
        calls = []
        def start(executable, arguments):
            calls.append(executable.read_bytes())
            if len(calls) == 1:
                raise OSError('mock process start failure')
            with self.frozen():
                self.assertEqual(updater.confirm_update_startup(arguments[1])['status'], 'rolled_back')
            return Child()
        result = updater._perform_update(self.manifest, _start=start, _wait=lambda pid: None)
        self.assertEqual(result, 'rolled_back')
        self.assertEqual(calls, [self.new, self.old])
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(self.state()['status'], 'rolled_back')

    def test_ready_timeout_stops_only_started_child_then_restores_old_file(self):
        child = Child()
        calls = []
        stops = []
        def start(executable, arguments):
            calls.append(executable.read_bytes())
            return child
        result = updater._perform_update(self.manifest, _start=start, _wait=lambda pid: None,
                                        _stop=lambda process: stops.append(process), _ready_timeout=0)
        self.assertEqual(result, 'rolled_back')
        self.assertEqual(stops, [child])
        self.assertEqual(calls, [self.new, self.old])
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_early_child_exit_restores_old_file(self):
        result = updater._perform_update(self.manifest, _start=lambda exe, args: Child(exited=True),
                                        _wait=lambda pid: None, _stop=lambda process: None,
                                        _ready_timeout=1)
        self.assertEqual(result, 'rolled_back')
        self.assertEqual(self.target.read_bytes(), self.old)

    def test_post_ready_result_or_cleanup_failure_cannot_undo_working_update(self):
        real_state = updater._state
        real_unlink = Path.unlink
        def state(root, data, status, message=''):
            if status == 'updated':
                raise OSError('test disk full')
            return real_state(root, data, status, message)
        def unlink(path, *args, **kwargs):
            if path.name.endswith(('.backup.exe', '.new.exe')):
                raise OSError('test antivirus lock')
            return real_unlink(path, *args, **kwargs)
        def start(executable, arguments):
            with self.frozen():
                updater.confirm_update_startup(arguments[1])
            return Child()
        with patch.object(updater, '_state', state), patch.object(Path, 'unlink', unlink):
            result = updater._perform_update(self.manifest, _start=start,
                                            _wait=lambda pid: None, _ready_timeout=1)
        self.assertEqual(result, 'updated')
        self.assertEqual(self.target.read_bytes(), self.new)

    def test_rollback_restarts_old_even_when_result_record_cannot_be_written(self):
        real_state = updater._state
        calls = []
        def state(root, data, status, message=''):
            if status in {'rolled_back', 'recovery_required', 'failed'}:
                raise OSError('test full staging disk')
            return real_state(root, data, status, message)
        def start(executable, arguments):
            calls.append(executable.read_bytes())
            if len(calls) == 1:
                raise OSError('test startup failure')
            return Child()
        with patch.object(updater, '_state', state):
            result = updater._perform_update(self.manifest, _start=start, _wait=lambda pid: None)
        self.assertEqual(result, 'rolled_back')
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(calls, [self.new, self.old])

    def test_failed_rollback_keeps_old_backup_for_manual_recovery(self):
        real_replace = updater._replace_with_retry
        def replace(source, target):
            if source.name.endswith('.backup.exe'):
                raise updater.UpdateError('test restore lock')
            return real_replace(source, target)
        with patch.object(updater, '_replace_with_retry', replace):
            result = updater._perform_update(self.manifest,
                _start=lambda exe, args: Child(exited=True), _wait=lambda pid: None,
                _stop=lambda process: None, _ready_timeout=1)
        self.assertEqual(result, 'recovery_required')
        backups = list(self.installed.glob('*.backup.exe'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.old)
        self.assertEqual(self.state()['status'], 'recovery_required')

    def test_stage_cleanup_waits_for_helper_exit_and_removes_only_known_files(self):
        (self.root/'helper.exe').write_bytes(self.old)
        updater._state(self.root, self.data, 'updated')
        state = updater._read_json(self.root/'state.json')
        state['helper_pid'] = 999999999
        updater._write_json(self.root/'state.json', state)
        updater._cleanup_completed_stage(self.root, self.data)
        self.assertFalse(self.root.exists())

    def test_recovery_required_stage_is_preserved(self):
        updater._state(self.root, self.data, 'recovery_required')
        updater._cleanup_completed_stage(self.root, self.data)
        self.assertTrue(self.manifest.exists())
        self.assertTrue((self.root/self.info['asset_name']).exists())

    def test_changed_installed_or_tampered_payload_is_never_overwritten(self):
        for mutate in ('target', 'payload'):
            with self.subTest(mutate=mutate):
                self.target.write_bytes(self.old)
                (self.root/self.info['asset_name']).write_bytes(self.new)
                if mutate == 'target':
                    self.target.write_bytes(pe_bytes(b'other update'))
                else:
                    (self.root/self.info['asset_name']).write_bytes(pe_bytes(b'tampered'))
                before = self.target.read_bytes()
                result = updater._perform_update(self.manifest, _start=lambda exe, args: Child(),
                                                _wait=lambda pid: None)
                self.assertEqual(result, 'failed')
                self.assertEqual(self.target.read_bytes(), before)

    def test_ready_requires_installed_frozen_identity_matching_payload(self):
        updater._state(self.root, self.data, 'awaiting_ready')
        with self.assertRaises(updater.UpdateError):
            updater.confirm_update_startup(self.manifest)
        with self.frozen(), self.assertRaises(updater.UpdateError):
            updater.confirm_update_startup(self.manifest)
        self.target.write_bytes(self.new)
        with self.frozen(executable=self.root/'helper.exe'), self.assertRaises(updater.UpdateError):
            updater.confirm_update_startup(self.manifest)
        with self.frozen():
            self.assertEqual(updater.confirm_update_startup(self.manifest)['status'], 'updated')
        self.assertEqual(updater._read_json(self.root/'ready.json')['token'], self.data['token'])

    def test_source_interpreter_can_never_be_replaced(self):
        with self.assertRaises(updater.UpdateError):
            updater.launch_update(self.root/self.info['asset_name'], self.info)
        with self.frozen(), self.assertRaises(updater.UpdateError):
            updater.launch_update(self.root/self.info['asset_name'], self.info,
                                  target=self.installed/'other.exe')

    def test_launcher_passes_only_helper_flag_manifest_and_copies_original(self):
        info = update_info(self.new)
        with self.frozen(), patch.object(updater, '_spawn') as spawn:
            manifest = updater.launch_update(self.root/info['asset_name'], info)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual((self.root/'helper.exe').read_bytes(), self.old)
        executable, arguments = spawn.call_args.args
        self.assertEqual(executable, self.root/'helper.exe')
        self.assertEqual(arguments, ['--apply-update', self.manifest])
        self.assertNotIn('password', json.dumps(updater._read_json(manifest)).lower())

    def test_readiness_rejects_accidentally_mislabelled_version_or_language(self):
        self.target.write_bytes(self.new)
        updater._state(self.root, self.data, 'awaiting_ready')
        for change in ({'VERSION': '0.0.1'}, {'LANGUAGE': 'en'}):
            with self.subTest(change=change), self.frozen(), patch.multiple(updater, **change):
                with self.assertRaises(updater.UpdateError):
                    updater.confirm_update_startup(self.manifest)

    def test_helper_refuses_source_or_wrong_executable_identity(self):
        with self.assertRaises(updater.UpdateError):
            updater.run_update_helper(self.manifest)
        with self.frozen(), self.assertRaises(updater.UpdateError):
            updater.run_update_helper(self.manifest)

    def test_os_lock_excludes_overlapping_transactions(self):
        with updater._target_lock(self.target), patch.object(updater, 'LOCK_TIMEOUT', 0):
            with self.assertRaises(updater.UpdateError):
                with updater._target_lock(self.target):
                    self.fail('second lock unexpectedly acquired')

    def test_invalid_manifest_paths_and_tokens_are_rejected(self):
        for field, value in [('target', 'relative.exe'), ('target', str(self.installed/'python')),
                             ('token', '../evil'), ('original_sha256', 'bad'), ('parent_pid', True)]:
            data = copy.deepcopy(self.data)
            data[field] = value
            updater._write_json(self.manifest, data)
            with self.subTest(field=field), self.assertRaises(updater.UpdateError):
                updater._load_manifest(self.manifest)

    def test_restart_process_has_independent_pyinstaller_environment(self):
        with patch.object(updater.subprocess, 'Popen') as popen:
            updater._spawn(self.target, ['--update-result', self.manifest])
        arguments, options = popen.call_args
        self.assertEqual(arguments[0], [str(self.target), '--update-result', str(self.manifest)])
        self.assertEqual(options['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertTrue(options['close_fds'])
        if os.name == 'nt':
            self.assertEqual(options['creationflags'], updater.subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    unittest.main()
