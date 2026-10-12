"""Tk-free update UI tests; no network, installed-program or CSE mutations."""
import queue
import threading
import unittest
from unittest.mock import Mock, patch

import app


class Value:
    def __init__(self, value=''):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class Widget:
    """Concrete widget state avoids truthy Mock defaults masking UI errors."""
    def __init__(self):
        self.options = {'state': 'normal'}
        self.visible = False
        self.stopped = False
        self.removed = False

    def configure(self, **options):
        self.options.update(options)

    def pack(self, **options):
        self.visible = True

    def pack_forget(self):
        self.visible = False

    def stop(self):
        self.stopped = True

    def grid_remove(self):
        self.removed = True


class Root:
    def __init__(self):
        self.callbacks = []
        self.destroyed = False
        self.display_flushed = False

    def after(self, delay, callback):
        self.callbacks.append((delay, callback))

    def destroy(self):
        self.destroyed = True

    def update_idletasks(self):
        self.display_flushed = True


class Session:
    def __init__(self):
        self.connected = True
        self.closed = False

    def close(self):
        self.closed = True


class DownloadPath:
    def __init__(self, cleanup_error=None):
        self.deleted = False
        self.cleanup_error = cleanup_error

    def unlink(self, *, missing_ok=False):
        if not missing_ok:
            raise AssertionError('Download cleanup must tolerate an absent file.')
        if self.cleanup_error:
            raise self.cleanup_error
        self.deleted = True


class CapturedThread:
    def __init__(self, target, daemon):
        self.target = target
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


def make_app():
    instance = app.App.__new__(app.App)
    instance.root = Root()
    instance.session = Session()
    instance.events = queue.Queue()
    instance.busy = False
    instance.operation = ''
    instance.updating = False
    instance.closing = False
    instance.close_when_download_finishes = False
    instance.checking_updates = False
    instance.update_cancel = threading.Event()
    instance.pending_update_prompt = False
    instance.prompted_update_version = None
    instance.available_update = {'available': True, 'version': '2.0.0',
                                 'size': 1024, 'asset_name': 'CSE-Space-Helper-zh.exe'}
    instance.selected = {'old-extension'}
    instance.rows = {'old-extension': {}}
    instance.report = {'items': []}
    instance.report_path = None
    instance.password = Value('password-kept-only-for-this-test')
    instance.status = Value()
    instance.update_status = Value()
    instance.scale = 1
    for name in ('scan_button', 'user_entry', 'pass_entry', 'disconnect_button', 'clean_button',
                 'select_button', 'export_button', 'update_button', 'download_button',
                 'cancel_update_button', 'update_progress', 'spinner'):
        setattr(instance, name, Widget())
    instance.render = Mock()
    instance.selection_changed = Mock()
    return instance


class AppUpdateTests(unittest.TestCase):
    def setUp(self):
        self.instance = make_app()
        self.threads = []

        def thread_factory(*, target, daemon):
            thread = CapturedThread(target, daemon)
            self.threads.append(thread)
            return thread

        patches = {
            'frozen': patch.object(app.sys, 'frozen', True, create=True),
            'platform': patch.object(app.sys, 'platform', 'win32'),
            'download': patch.object(app, 'download_update'),
            'check': patch.object(app, 'check_release'),
            'launch': patch.object(app, 'launch_update'),
            'discard': patch.object(app, 'discard_download',
                                    side_effect=lambda path: path.unlink(missing_ok=True)),
            'thread': patch.object(app.threading, 'Thread', side_effect=thread_factory),
            'prompt': patch.object(app.messagebox, 'askyesno', return_value=False),
            'error': patch.object(app.messagebox, 'showerror'),
            'info': patch.object(app.messagebox, 'showinfo'),
        }
        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def assert_running_normally(self):
        instance = self.instance
        self.assertFalse(instance.updating)
        self.assertFalse(instance.closing)
        self.assertFalse(instance.root.destroyed)
        self.assertFalse(instance.session.closed)
        self.assertEqual(instance.update_button.options['state'], 'normal')
        self.assertEqual(instance.scan_button.options['state'], 'normal')
        self.assertEqual(instance.user_entry.options['state'], 'disabled')
        self.assertEqual(instance.clean_button.options['state'], 'normal')
        self.assertFalse(instance.cancel_update_button.visible)
        self.assertFalse(instance.update_progress.visible)
        self.assertTrue(instance.download_button.visible)

    def begin_download(self):
        self.prompt.return_value = True
        self.instance.open_update()
        self.assertEqual(len(self.threads), 1)
        self.assertTrue(self.threads[0].started)
        self.assertTrue(self.threads[0].daemon)

    def test_automatic_release_result_defers_prompt_until_cleanup_finishes(self):
        instance = self.instance
        instance.busy = True
        instance.operation = 'clean'
        instance.checking_updates = True
        instance.events.put(('update', instance.available_update, False))

        instance.poll()

        self.assertTrue(instance.busy)
        self.assertEqual(instance.operation, 'clean')
        self.assertTrue(instance.pending_update_prompt)
        self.assertFalse(instance.checking_updates)
        self.assertFalse(instance.spinner.stopped)
        self.assertFalse(instance.spinner.removed)
        self.prompt.assert_not_called()
        self.launch.assert_not_called()
        self.download.assert_not_called()
        self.assertFalse(instance.session.closed)
        self.assertEqual(len(instance.root.callbacks), 1)

        report = {'items': ['cleanup-finished']}
        instance.events.put(('report', report, 'report-path'))
        instance.poll()

        self.assertFalse(instance.busy)
        self.assertFalse(instance.pending_update_prompt)
        self.assertTrue(instance.spinner.stopped)
        instance.render.assert_called_once_with(report)
        self.assertEqual(instance.report_path, 'report-path')
        self.prompt.assert_called_once()
        self.download.assert_not_called()

    def test_declining_never_starts_download_or_installer(self):
        instance = self.instance
        instance.open_update()

        self.prompt.assert_called_once()
        self.thread.assert_not_called()
        self.download.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse(instance.updating)
        self.assertFalse(instance.root.destroyed)
        self.assertEqual(instance.prompted_update_version, '2.0.0')
        self.assertIn('稍后', instance.update_status.get())

    def test_check_runs_once_in_background_then_delivers_prompt(self):
        instance = self.instance
        self.check.return_value = dict(instance.available_update)
        instance.available_update = None

        instance.check_updates()
        instance.check_updates()

        self.assertTrue(instance.checking_updates)
        self.assertEqual(instance.update_button.options['state'], 'disabled')
        self.assertEqual(instance.download_button.options['state'], 'disabled')
        self.assertEqual(len(self.threads), 1)
        self.check.assert_not_called()
        self.threads[0].target()
        instance.poll()

        self.check.assert_called_once_with()
        self.prompt.assert_called_once()
        self.assertFalse(instance.checking_updates)
        self.assertEqual(instance.update_button.options['state'], 'normal')
        self.assertEqual(instance.download_button.options['state'], 'normal')
        self.download.assert_not_called()

    def test_check_pending_prevents_installing_previously_detected_release(self):
        instance = self.instance
        self.prompt.return_value = True
        instance.check_updates()

        instance.open_update()

        self.assertTrue(instance.checking_updates)
        self.assertFalse(instance.updating)
        self.assertEqual(len(self.threads), 1)
        self.prompt.assert_not_called()
        self.download.assert_not_called()
        self.launch.assert_not_called()

    def test_automatic_check_error_is_quiet_and_keeps_cleanup_running(self):
        instance = self.instance
        instance.busy = True
        instance.operation = 'clean'
        self.check.side_effect = TimeoutError('release service unavailable')

        instance.check_updates()
        self.threads[0].target()
        instance.poll()

        self.assertTrue(instance.busy)
        self.assertEqual(instance.operation, 'clean')
        self.assertFalse(instance.checking_updates)
        self.assertFalse(instance.spinner.stopped)
        self.prompt.assert_not_called()
        self.error.assert_not_called()
        self.download.assert_not_called()

    def test_automatic_checks_do_not_repeat_declined_version_prompt(self):
        instance = self.instance
        instance.open_update()
        self.prompt.assert_called_once()

        instance.finish_update('update', instance.available_update, manual=False)

        self.assertFalse(instance.pending_update_prompt)
        instance.finish_update('update', instance.available_update, manual=True)
        self.assertTrue(instance.pending_update_prompt)

    def test_source_mode_cannot_replace_python_interpreter(self):
        self.prompt.return_value = True
        with patch.object(app.sys, 'frozen', False):
            self.instance.open_update()

        self.info.assert_called_once()
        self.prompt.assert_not_called()
        self.thread.assert_not_called()
        self.download.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse(self.instance.updating)

    def test_confirmation_starts_background_download_and_blocks_scan_cleanup(self):
        instance = self.instance
        path = DownloadPath()

        def download(info, *, progress, cancel_event):
            self.assertEqual(info, instance.available_update)
            self.assertIsNot(info, instance.available_update)
            self.assertIs(cancel_event, instance.update_cancel)
            progress(512, 1024)
            return path

        self.download.side_effect = download
        self.begin_download()

        self.assertTrue(instance.updating)
        self.assertEqual(instance.update_button.options['state'], 'disabled')
        self.assertEqual(instance.scan_button.options['state'], 'disabled')
        self.assertEqual(instance.user_entry.options['state'], 'disabled')
        self.assertEqual(instance.clean_button.options['state'], 'disabled')
        self.assertTrue(instance.cancel_update_button.visible)
        self.assertTrue(instance.update_progress.visible)
        self.download.assert_not_called()

        self.threads[0].target()

        self.download.assert_called_once()
        self.assertEqual(instance.events.get_nowait(), ('download_progress', (512, 1024), None))
        kind, result, context = instance.events.get_nowait()
        self.assertEqual(kind, 'download_complete')
        self.assertIs(result[0], path)
        self.assertEqual(result[1], instance.available_update)
        self.assertIsNone(context)
        self.launch.assert_not_called()

    def test_download_progress_does_not_finish_running_operation(self):
        instance = self.instance
        instance.busy = True
        instance.operation = 'clean'
        instance.updating = True
        instance.events.put(('download_progress', (256, 1024), None))

        instance.poll()

        self.assertTrue(instance.busy)
        self.assertTrue(instance.updating)
        self.assertEqual(instance.operation, 'clean')
        self.assertEqual(instance.update_progress.options['value'], 25)
        self.assertFalse(instance.spinner.stopped)
        self.assertFalse(instance.spinner.removed)
        self.launch.assert_not_called()

    def test_cancelled_worker_restores_ui_without_installing(self):
        self.download.side_effect = app.UpdateCancelled()
        self.begin_download()
        self.instance.cancel_update()
        self.threads[0].target()

        self.instance.poll()

        self.assert_running_normally()
        self.assertTrue(self.instance.update_cancel.is_set())
        self.launch.assert_not_called()
        self.error.assert_not_called()

    def test_cancel_after_complete_was_queued_still_does_not_install(self):
        path = DownloadPath()
        self.download.return_value = path
        self.begin_download()
        self.threads[0].target()
        self.instance.cancel_update()

        self.instance.poll()

        self.assertTrue(path.deleted)
        self.assert_running_normally()
        self.launch.assert_not_called()
        self.error.assert_not_called()

    def test_close_during_download_waits_for_worker_cancellation_before_destroy(self):
        instance = self.instance
        self.download.side_effect = app.UpdateCancelled()
        self.begin_download()

        instance.close()

        self.assertTrue(instance.update_cancel.is_set())
        self.assertTrue(instance.close_when_download_finishes)
        self.assertTrue(instance.updating)
        self.assertFalse(instance.closing)
        self.assertFalse(instance.root.destroyed)
        self.assertFalse(instance.session.closed)
        self.assertNotEqual(instance.password.get(), '')
        self.assertEqual(instance.cancel_update_button.options['state'], 'disabled')

        status = instance.update_status.get()
        instance.events.put(('download_progress', (512, 1024), None))
        instance.poll()
        self.assertEqual(instance.update_status.get(), status)
        self.assertFalse(instance.root.destroyed)
        callback_count = len(instance.root.callbacks)

        self.threads[0].target()
        instance.poll()

        self.assertFalse(instance.updating)
        self.assertTrue(instance.closing)
        self.assertTrue(instance.root.destroyed)
        self.assertTrue(instance.session.closed)
        self.assertEqual(instance.password.get(), '')
        self.assertEqual(len(instance.root.callbacks), callback_count)
        self.launch.assert_not_called()
        self.error.assert_not_called()

    def test_close_with_completed_download_queued_discards_then_closes_without_install(self):
        instance = self.instance
        path = DownloadPath()
        self.download.return_value = path
        self.begin_download()
        self.threads[0].target()

        instance.close()
        self.assertFalse(instance.root.destroyed)
        instance.poll()

        self.discard.assert_called_once_with(path)
        self.assertTrue(path.deleted)
        self.assertTrue(instance.closing)
        self.assertTrue(instance.root.destroyed)
        self.assertTrue(instance.session.closed)
        self.assertFalse(instance.updating)
        self.assertEqual(instance.password.get(), '')
        self.assertEqual(instance.root.callbacks, [])
        self.launch.assert_not_called()
        self.error.assert_not_called()

    def test_declining_close_keeps_download_running(self):
        instance = self.instance
        self.begin_download()
        self.prompt.return_value = False

        instance.close()

        self.assertTrue(instance.updating)
        self.assertFalse(instance.close_when_download_finishes)
        self.assertFalse(instance.update_cancel.is_set())
        self.assertFalse(instance.closing)
        self.assertFalse(instance.root.destroyed)
        self.assertFalse(instance.session.closed)

    def test_download_failure_restores_buttons_and_offers_retry(self):
        self.download.side_effect = OSError('download failed')
        self.begin_download()
        self.threads[0].target()

        self.instance.poll()

        self.assert_running_normally()
        self.assertIn('重试', self.instance.update_status.get())
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1], 'download failed')
        self.launch.assert_not_called()

    def test_failed_helper_spawn_keeps_current_app_and_session_alive(self):
        path = DownloadPath()
        self.download.return_value = path
        self.launch.side_effect = OSError('helper could not start')
        self.begin_download()
        self.threads[0].target()

        self.instance.poll()

        self.launch.assert_called_once_with(path, self.instance.available_update)
        self.assertTrue(path.deleted)
        self.assert_running_normally()
        self.assertNotEqual(self.instance.password.get(), '')
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1], 'helper could not start')

    def test_cancel_cleanup_failure_still_restores_ui(self):
        path = DownloadPath(cleanup_error=PermissionError('download still in use'))
        self.download.return_value = path
        self.begin_download()
        self.threads[0].target()
        self.instance.cancel_update()

        self.instance.poll()

        self.assert_running_normally()
        self.launch.assert_not_called()

    def test_failed_helper_cleanup_failure_still_restores_ui(self):
        path = DownloadPath(cleanup_error=PermissionError('download still in use'))
        self.download.return_value = path
        self.launch.side_effect = OSError('helper could not start')
        self.begin_download()
        self.threads[0].target()

        self.instance.poll()

        self.assert_running_normally()
        self.error.assert_called_once()
        self.assertEqual(self.error.call_args.args[1], 'helper could not start')

    def test_successful_handoff_clears_password_closes_session_and_window(self):
        path = DownloadPath()
        self.download.return_value = path
        self.begin_download()
        self.threads[0].target()

        self.instance.poll()

        self.launch.assert_called_once_with(path, self.instance.available_update)
        self.assertFalse(path.deleted)
        self.assertEqual(self.instance.password.get(), '')
        self.assertTrue(self.instance.session.closed)
        self.assertTrue(self.instance.closing)
        self.assertTrue(self.instance.root.destroyed)
        self.assertTrue(self.instance.root.display_flushed)
        self.assertEqual(self.instance.root.callbacks, [])
        self.error.assert_not_called()


if __name__ == '__main__':
    unittest.main()
