"""Verified public downloads and transactional frozen-application updates.

The helper is a private copy of the installed executable. It receives only a
local manifest, never connection arguments or CSE credentials. The old binary
is kept until the new application's UI confirms successful startup.
"""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from build_info import VERSION, LANGUAGE, REPOSITORY

API_URL = 'https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
RELEASE_ROOT = 'https://github.com/'+REPOSITORY+'/releases/'
MAX_RESPONSE = 1024*1024
MAX_ASSET_SIZE = 512*1024*1024
CHUNK_SIZE = 256*1024
STAGE_PREFIX = 'cse-space-helper-update-'
EXIT_TIMEOUT = 90
READY_TIMEOUT = 90
LOCK_TIMEOUT = 15
ASSET_HOSTS = {'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
               'github-releases.githubusercontent.com'}


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or len(value) > 32:
        raise UpdateError('更新版本号无效。')
    match = re.fullmatch(r'v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value)
    if not match:
        raise UpdateError('更新版本号无效。')
    return tuple(map(int, match.groups()))


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', value):
        raise UpdateError('新版下载文件缺少有效的 SHA-256 校验信息。')
    return value[7:].lower()


def release_update(release, current=VERSION, language=LANGUAGE):
    if language not in {'zh', 'en'}:
        raise UpdateError('更新语言无效。')
    if not isinstance(release, dict):
        raise UpdateError('更新服务返回了无效内容。')
    if release.get('draft') is not False or release.get('prerelease') is not False:
        return {'available': False, 'version': current}
    tag = release.get('tag_name')
    latest = version_tuple(tag)
    if latest <= version_tuple(current):
        return {'available': False, 'version': current}
    page = RELEASE_ROOT+'tag/'+tag
    if release.get('html_url') != page:
        raise UpdateError('更新链接未通过验证。')
    name = 'CSE-Space-Helper-'+language+'.exe'
    assets = release.get('assets')
    if not isinstance(assets, list):
        raise UpdateError('新版下载文件尚未准备完成。')
    matches = [item for item in assets if isinstance(item, dict) and item.get('name') == name]
    if len(matches) != 1:
        raise UpdateError('新版下载文件尚未准备完成。')
    asset = matches[0]
    url = RELEASE_ROOT+'download/'+tag+'/'+name
    if (asset.get('browser_download_url') != url or asset.get('state') != 'uploaded'
            or type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_ASSET_SIZE):
        raise UpdateError('新版下载文件未通过验证。')
    return {'available': True, 'version': tag.removeprefix('v'), 'page_url': page,
            'download_url': url, 'asset_name': name, 'size': asset['size'],
            'sha256': _digest(asset.get('digest'))}


def check_release(current=VERSION, language=LANGUAGE, opener=None):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(API_URL, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'CSE-Space-Helper/'+VERSION,
        'X-GitHub-Api-Version': '2026-03-10',
    })
    try:
        with opener(request, timeout=8) as response:
            if response.geturl() != API_URL:
                raise UpdateError('更新服务地址发生变化，请访问项目发布页。')
            content = response.read(MAX_RESPONSE+1)
        if len(content) > MAX_RESPONSE:
            raise UpdateError('更新服务返回内容过大。')
        release = json.loads(content.decode('utf-8'))
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {403, 429}:
            raise UpdateError('更新服务暂时限制请求，请稍后重试。') from exc
        if exc.code == 404:
            raise UpdateError('暂时找不到公开发行版，请稍后重试。') from exc
        raise UpdateError('暂时无法连接更新服务，请稍后重试。') from exc
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise UpdateError('暂时无法连接更新服务，请稍后重试。') from exc
    except (UnicodeError, ValueError) as exc:
        raise UpdateError('更新服务返回了无效内容。') from exc
    return release_update(release, current, language)


def _validate_info(info):
    if not isinstance(info, dict) or info.get('available') is not True:
        raise UpdateError('新版下载文件未通过验证。')
    version = info.get('version')
    version_tuple(version)
    if version.startswith('v'):
        raise UpdateError('更新版本号无效。')
    name = info.get('asset_name')
    if name not in {'CSE-Space-Helper-zh.exe', 'CSE-Space-Helper-en.exe'}:
        raise UpdateError('新版下载文件未通过验证。')
    if info.get('download_url') not in {
            RELEASE_ROOT+'download/'+version+'/'+name,
            RELEASE_ROOT+'download/v'+version+'/'+name}:
        raise UpdateError('更新链接未通过验证。')
    if type(info.get('size')) is not int or not 0 < info['size'] <= MAX_ASSET_SIZE:
        raise UpdateError('新版下载文件未通过验证。')
    _digest('sha256:'+str(info.get('sha256', '')))


def _asset_url_allowed(url, initial):
    try:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in {None, 443}):
            return False
        return url == initial or parsed.hostname in ASSET_HOSTS
    except (ValueError, TypeError):
        return False


class _AssetRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, initial):
        self.initial = initial

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _asset_url_allowed(newurl, self.initial):
            raise UpdateError('新版下载地址未通过验证。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _asset_opener(initial):
    return urllib.request.build_opener(_AssetRedirectHandler(initial)).open


def _verify_pe(path):
    with open(path, 'rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise UpdateError('下载内容不是有效的 Windows 应用程序。')
        offset = int.from_bytes(header[60:64], 'little')
        if not 64 <= offset <= min(path.stat().st_size-4, 1024*1024):
            raise UpdateError('下载内容不是有效的 Windows 应用程序。')
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise UpdateError('下载内容不是有效的 Windows 应用程序。')


def _file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_payload(path, info):
    _validate_info(info)
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != info['size']
            or _file_hash(path) != info['sha256'].lower()):
        raise UpdateError('下载文件校验失败，请重新下载。')
    _verify_pe(path)


def discard_download(path):
    """Remove only the known staging files created by download_update."""
    path = Path(path)
    root = path.parent
    if (root.name.startswith(STAGE_PREFIX) and not root.is_symlink()
            and path.name in {'CSE-Space-Helper-zh.exe', 'CSE-Space-Helper-en.exe'}):
        try:
            path.unlink(missing_ok=True)
            path.with_suffix('.part').unlink(missing_ok=True)
            root.rmdir()
        except OSError:
            pass


def download_update(info, progress=None, cancel_event=None, *, _opener=None):
    """Stream a bounded asset, verifying size, hash and PE header.

    ``_opener`` injects an offline test network; it is not a production CLI flag.
    No unverified downloaded file is ever launched.
    """
    _validate_info(info)
    root = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX))
    path = root/info['asset_name']
    partial = path.with_suffix('.part')
    opener = _opener or _asset_opener(info['download_url'])
    request = urllib.request.Request(info['download_url'], headers={
        'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity',
        'User-Agent': 'CSE-Space-Helper/'+VERSION})
    digest = hashlib.sha256()
    received = 0

    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelled('更新下载已取消。')

    try:
        cancelled()
        with opener(request, timeout=20) as response, open(partial, 'xb') as output:
            if not _asset_url_allowed(response.geturl(), info['download_url']):
                raise UpdateError('新版下载地址未通过验证。')
            length = response.headers.get('Content-Length')
            if length is not None and (not length.isdecimal() or int(length) != info['size']):
                raise UpdateError('下载文件大小与发行版信息不一致。')
            if progress:
                progress(0, info['size'])
            while True:
                cancelled()
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
                if received > info['size']:
                    raise UpdateError('下载文件大小与发行版信息不一致。')
                digest.update(chunk)
                output.write(chunk)
                if progress:
                    progress(received, info['size'])
            output.flush()
            os.fsync(output.fileno())
        cancelled()
        if received != info['size'] or digest.hexdigest() != info['sha256'].lower():
            raise UpdateError('下载文件校验失败，请重新下载。')
        _verify_pe(partial)
        os.replace(partial, path)
        return path
    except UpdateError:
        discard_download(path)
        raise
    except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
        discard_download(path)
        raise UpdateError('下载更新失败，请检查网络和可用磁盘空间后重试。') from exc
    except Exception:
        discard_download(path)
        raise


def _write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path):
    with open(path, 'rb') as stream:
        content = stream.read(16385)
    if len(content) > 16384:
        raise UpdateError('本地更新记录无效。')
    result = json.loads(content.decode('utf-8'))
    if not isinstance(result, dict):
        raise UpdateError('本地更新记录无效。')
    return result


def _load_manifest(path):
    path = Path(path).resolve()
    try:
        data = _read_json(path)
        root = path.parent
        if (path.name != 'update.json' or not root.name.startswith(STAGE_PREFIX)
                or not re.fullmatch(r'[0-9a-f]{64}', data.get('token', ''))
                or type(data.get('parent_pid')) is not int or data['parent_pid'] <= 0
                or not re.fullmatch(r'[0-9a-f]{64}', data.get('original_sha256', ''))):
            raise ValueError()
        target = Path(data['target'])
        if (not target.is_absolute() or target != target.resolve()
                or target.suffix.lower() != '.exe' or target.parent == root):
            raise ValueError()
        _validate_info(data['info'])
        version_tuple(data['original_version'])
        return data, root, target
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise UpdateError('本地更新记录无效。') from exc


def _independent_environment():
    environment = os.environ.copy()
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return environment


def _spawn(executable, arguments):
    options = {'env': _independent_environment(), 'close_fds': True,
               'cwd': str(executable.parent)}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    return subprocess.Popen([str(executable), *map(str, arguments)], **options)


def launch_update(download_path, info, target=None):
    """Start a copied frozen helper; caller must then close the application.

    Only the current frozen executable can be replaced. Source Python runs can
    never replace a Python interpreter with an application download.
    """
    if not getattr(sys, 'frozen', False):
        raise UpdateError('自动安装更新仅适用于打包后的 EXE 程序。')
    _validate_info(info)
    if version_tuple(info['version']) <= version_tuple(VERSION):
        raise UpdateError('更新版本号无效。')
    executable = Path(sys.executable).resolve()
    target = Path(target).resolve() if target is not None else executable
    if target != executable or target.suffix.lower() != '.exe':
        raise UpdateError('当前程序路径未通过验证。')
    path = Path(download_path).resolve()
    root = path.parent
    if not root.name.startswith(STAGE_PREFIX) or path.name != info.get('asset_name'):
        raise UpdateError('本地更新记录无效。')
    _verify_payload(path, info)
    _verify_pe(target)
    helper = root/'helper.exe'
    manifest = root/'update.json'
    token = secrets.token_hex(32)
    probe = target.parent/('.'+target.name+'.'+token+'.write-test')
    try:
        with open(probe, 'xb'):
            pass
        probe.unlink()
        shutil.copy2(target, helper)
        data = {'token': token, 'parent_pid': os.getpid(), 'target': str(target),
                'original_sha256': _file_hash(target), 'original_version': VERSION,
                'info': dict(info)}
        _write_json(manifest, data)
        _spawn(helper, ['--apply-update', manifest])
        return manifest
    except OSError as exc:
        probe.unlink(missing_ok=True)
        raise UpdateError('无法启动更新，请确认程序所在文件夹可写。') from exc


def _process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_parent(pid):
    deadline = time.monotonic()+EXIT_TIMEOUT
    while _process_alive(pid):
        if time.monotonic() >= deadline:
            raise UpdateError('旧版程序未能退出，更新已停止。')
        time.sleep(.2)


@contextmanager
def _target_lock(target):
    """OS-held locks expire with the helper, including after a crash."""
    lock = target.parent/('.'+target.name+'.update.lock')
    with open(lock, 'a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        deadline = time.monotonic()+LOCK_TIMEOUT
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise UpdateError('另一个更新正在进行，请稍后重试。') from exc
                time.sleep(.2)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _replace_with_retry(source, target):
    # The onefile bootloader may retain its EXE lock after the Python PID exits.
    deadline = time.monotonic()+EXIT_TIMEOUT
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise UpdateError('无法替换程序文件，请关闭其他程序窗口后重试。') from exc
            time.sleep(.2)


def _stop_child(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # /T covers this bootloader and its Python child; no other app is stopped.
        system = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'/'taskkill.exe'
        subprocess.run([str(system), '/PID', str(process.pid), '/T', '/F'],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


def _state(root, data, status, message=''):
    _write_json(root/'state.json', {'token': data['token'], 'status': status,
                                  'version': data['info']['version'], 'message': message,
                                  'helper_pid': os.getpid()})


def _best_effort_state(root, data, status, message=''):
    try:
        _state(root, data, status, message)
    except OSError:
        # Result recording must not stop recovery or undo a successful startup.
        pass


def _cleanup_completed_stage(root, data):
    """Delete only manifest-owned files after the copied helper has exited.

    Run in the new/restored app. Retried unlinks also cover the separate onefile
    bootloader's executable lock. Recovery-required artifacts are preserved.
    """
    deadline = time.monotonic()+EXIT_TIMEOUT+READY_TIMEOUT+30
    files = [root/data['info']['asset_name'], root/'helper.exe', root/'ready.json',
             root/'ready.tmp', root/'state.tmp', root/'update.tmp']
    while time.monotonic() < deadline:
        try:
            state = _read_json(root/'state.json')
            if state.get('token') != data['token'] or state.get('status') == 'recovery_required':
                return
            if (state.get('status') in {'updated', 'rolled_back', 'failed'}
                    and type(state.get('helper_pid')) is int
                    and not _process_alive(state['helper_pid'])):
                for path in files:
                    path.unlink(missing_ok=True)
                (root/'state.json').unlink(missing_ok=True)
                (root/'update.json').unlink(missing_ok=True)
                root.rmdir()
                return
        except (OSError, ValueError, UnicodeError, UpdateError):
            pass
        time.sleep(.5)


def _schedule_cleanup(root, data):
    threading.Thread(target=_cleanup_completed_stage, args=(root, data), daemon=True,
                     name='update-staging-cleanup').start()


def _wait_ready(root, data, process, timeout):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise UpdateError('新版程序启动失败，已恢复旧版。')
        try:
            ready = _read_json(root/'ready.json')
            if (ready.get('token') == data['token'] and ready.get('sha256') == data['info']['sha256']
                    and type(ready.get('pid')) is int and _process_alive(ready['pid'])):
                return
        except (OSError, ValueError, UnicodeError, UpdateError):
            pass
        time.sleep(.2)
    raise UpdateError('新版程序启动超时，已恢复旧版。')


def _perform_update(manifest_path, *, _start=None, _wait=None, _stop=None,
                    _ready_timeout=READY_TIMEOUT):
    """Transaction core with private injections for isolated replacement tests.

    run_update_helper enforces frozen identity. Tests exercise real file moves
    on temporary PE fixtures with fake children, never the installed app.
    """
    data, root, target = _load_manifest(manifest_path)
    start = _start or _spawn
    wait = _wait or _wait_parent
    stop = _stop or _stop_child
    candidate = target.parent/('.'+target.name+'.'+data['token']+'.new.exe')
    backup = target.parent/('.'+target.name+'.'+data['token']+'.backup.exe')
    process = None
    replaced = False
    try:
        with _target_lock(target):
            try:
                _state(root, data, 'waiting')
                wait(data['parent_pid'])
                _verify_payload(root/data['info']['asset_name'], data['info'])
                if _file_hash(target) != data['original_sha256']:
                    raise UpdateError('程序文件已发生变化，更新已停止。')
                shutil.copy2(target, backup)
                shutil.copy2(root/data['info']['asset_name'], candidate)
                shutil.copystat(target, candidate)
                _verify_payload(candidate, data['info'])
                _replace_with_retry(candidate, target)
                replaced = True
                _state(root, data, 'awaiting_ready')
                process = start(target, ['--update-result', Path(manifest_path).resolve()])
                _wait_ready(root, data, process, _ready_timeout)
                _best_effort_state(root, data, 'updated', '更新完成，已启动新版程序。')
                # Cleanup failures must never undo a working update.
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass
                return 'updated'
            except Exception as exc:
                message = str(exc) if isinstance(exc, UpdateError) else '安装更新失败。'
                if replaced:
                    try:
                        if process is not None:
                            stop(process)
                        _replace_with_retry(backup, target)
                    except Exception:
                        _best_effort_state(root, data, 'recovery_required',
                                           '自动恢复失败，请使用保留的旧版程序文件。'+'\n'+str(backup))
                        return 'recovery_required'
                    _best_effort_state(root, data, 'rolled_back', message)
                    try:
                        start(target, ['--update-result', Path(manifest_path).resolve()])
                    except Exception:
                        pass
                    return 'rolled_back'
                _best_effort_state(root, data, 'failed', message)
                # Restart only after the original process has actually exited.
                if not _process_alive(data['parent_pid']) and target.is_file():
                    try:
                        start(target, ['--update-result', Path(manifest_path).resolve()])
                    except OSError:
                        pass
                return 'failed'
            finally:
                try:
                    candidate.unlink(missing_ok=True)
                    if not replaced:
                        backup.unlink(missing_ok=True)
                except OSError:
                    pass
    except (OSError, UpdateError) as exc:
        _best_effort_state(root, data, 'failed', str(exc))
        return 'failed'


def run_update_helper(manifest_path):
    data, root, target = _load_manifest(manifest_path)
    if (not getattr(sys, 'frozen', False)
            or Path(sys.executable).resolve() != root/'helper.exe'
            or _file_hash(root/'helper.exe') != data['original_sha256']):
        raise UpdateError('当前程序路径未通过验证。')
    return _perform_update(manifest_path)


def confirm_update_startup(manifest_path):
    """Call once the UI is ready to acknowledge this exact new executable.

    An old executable may instead display a rollback/failure result.
    """
    data, root, target = _load_manifest(manifest_path)
    if not getattr(sys, 'frozen', False) or Path(sys.executable).resolve() != target:
        raise UpdateError('当前程序路径未通过验证。')
    state = _read_json(root/'state.json')
    if state.get('token') != data['token']:
        raise UpdateError('本地更新记录无效。')
    if state.get('status') == 'awaiting_ready':
        if (data['info']['version'] != VERSION
                or data['info']['asset_name'] != 'CSE-Space-Helper-'+LANGUAGE+'.exe'):
            raise UpdateError('下载文件校验失败，请重新下载。')
        _verify_payload(target, data['info'])
        _write_json(root/'ready.json', {'token': data['token'], 'pid': os.getpid(),
                                      'sha256': data['info']['sha256']})
        _schedule_cleanup(root, data)
        return {'status': 'updated', 'version': data['info']['version'],
                'message': '更新完成，已启动新版程序。'}
    if state.get('status') in {'rolled_back', 'failed', 'recovery_required', 'updated'}:
        if state['status'] != 'recovery_required':
            _schedule_cleanup(root, data)
        return state
    raise UpdateError('本地更新记录无效。')
