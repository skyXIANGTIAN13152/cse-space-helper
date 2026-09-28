"""CSE quota maintenance engine. Standard library only; runs over SSH on Linux.

A scan is read-only. Cleanup accepts only fresh, independently rediscovered
candidates, with matching recursive metadata fingerprints and anchored paths.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:
    fcntl = None

COMMIT = re.compile(r"^[0-9a-f]{40}$")
SERVER = re.compile(r"^Stable-([0-9a-f]{40})$")
EXTENSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{1,190}$")
MIN_AGE = 3600
LOG_AGE = 7 * 86400


class SafetyError(Exception):
    pass


def uid():
    return os.getuid() if hasattr(os, 'getuid') else None


def read_json(path):
    if path.stat().st_size > 8 * 1024 * 1024:
        raise SafetyError('配置文件异常过大，已停止解析。')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def parse_quota(text):
    def amount(word):
        m = re.fullmatch(r'(\d+(?:\.\d+)?)([KMGT]?)\*?', word, re.I)
        if not m:
            raise ValueError(word)
        # rquota/ quota -s use binary multiples; unitless fields are KiB.
        return int(float(m[1]) * {'': 1024, 'K': 1024, 'M': 1024**2,
                                 'G': 1024**3, 'T': 1024**4}[m[2].upper()])
    for line in text.splitlines():
        cols = line.split()
        if cols and cols[0].startswith('/') and len(cols) >= 4:
            try:
                used, soft, hard = map(amount, cols[1:4])
                return {'used': used, 'soft': soft, 'hard': hard,
                        'over': soft > 0 and used > soft, 'raw': text}
            except ValueError:
                continue
    return {'used': None, 'soft': None, 'hard': None, 'over': None, 'raw': text}


def quota_now():
    try:
        result = subprocess.run(['rquota'], capture_output=True, text=True,
                                timeout=25, env={**os.environ, 'LC_ALL': 'C'})
        if result.returncode == 0:
            return parse_quota(result.stdout)
        return parse_quota('暂时无法读取服务器配额。')
    except (OSError, subprocess.TimeoutExpired):
        return parse_quota('暂时无法读取服务器配额。')


def process_state(root):
    paths, count, uncertain = [], 0, False
    proc = Path('/proc')
    if not proc.is_dir():
        return {'paths': [], 'count': 0, 'uncertain': True}
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != uid():
                continue
            args = (entry / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            try:
                executable = os.readlink(entry / 'exe')
            except FileNotFoundError:
                continue
            except PermissionError:
                # Linux deliberately hides these privileged login helpers even
                # from their own user. Readable argv still rules out VS Code.
                name = (entry / 'comm').read_text().strip()
                first = args[0] if args else ''
                known_helper = (name == 'systemd' and first in ['/usr/lib/systemd/systemd', '/lib/systemd/systemd']) or (
                    name == '(sd-pam)' and first == '(sd-pam)') or (
                    name in ['sshd', 'sshd-session', 'sshd-auth'] and first.startswith(name+': '))
                if known_helper and not any('.vscode-server' in arg for arg in args):
                    continue
                uncertain = True
                executable = ''
            if any(str(root) in arg or '.vscode-server' in arg for arg in args + [executable]):
                paths.extend(args + [executable])
                count += 1
        except PermissionError:
            uncertain = True
        except (FileNotFoundError, ProcessLookupError):
            pass
    return {'paths': paths, 'count': count, 'uncertain': uncertain}


class Engine:
    def __init__(self, home, local_commit='', processes=None, quota=None, now=None):
        self.home = Path(home).resolve(strict=True)
        self.root = self.home / '.vscode-server'
        self.local_commit = local_commit if COMMIT.fullmatch(local_commit or '') else ''
        self._processes = processes
        self._quota = quota or quota_now
        self.now = now or time.time
        self.warnings = []

    def anchored(self, path):
        path = Path(path)
        if path == self.root or not path.is_relative_to(self.root):
            raise SafetyError('目标超出了允许清理的范围。')
        cursor = path
        while cursor != self.home:
            info = cursor.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise SafetyError('跳过符号链接或经由符号链接访问的目录。')
            if uid() is not None and info.st_uid != uid():
                raise SafetyError('目标不属于当前账户。')
            cursor = cursor.parent
        if not path.resolve(strict=True).is_relative_to(self.root.resolve(strict=True)):
            raise SafetyError('目标实际位置超出了允许清理的范围。')
        return path

    def config(self, path):
        self.anchored(path)
        return read_json(path)

    def fingerprint(self, path):
        """No symlink traversal; refuse mounts, foreign ownership and NFS busy files."""
        self.anchored(path)
        digest, size, newest, seen = hashlib.sha256(), 0, 0, set()
        device = path.lstat().st_dev
        stack = [path]
        while stack:
            item = stack.pop()
            info = item.lstat()
            newest = max(newest, info.st_mtime)
            if info.st_dev != device or (uid() is not None and info.st_uid != uid()):
                raise SafetyError('目标内包含其他文件系统或其他账户的文件。')
            if item.name.startswith('.nfs'):
                raise SafetyError('文件仍被网络文件系统占用，已跳过。')
            relative = str(item.relative_to(path))
            digest.update(json.dumps([relative, info.st_dev, info.st_ino, info.st_mode,
                                      info.st_size, info.st_mtime_ns, info.st_ctime_ns],
                                     ensure_ascii=True).encode())
            if (info.st_dev, info.st_ino) not in seen:
                size += getattr(info, 'st_blocks', (info.st_size + 511)//512) * 512
                seen.add((info.st_dev, info.st_ino))
            if stat.S_ISDIR(info.st_mode):
                if item != path and os.path.ismount(item):
                    raise SafetyError('目标内包含挂载点，已跳过。')
                stack.extend(sorted(item.iterdir(), reverse=True))
        return digest.hexdigest(), size, newest

    def proc(self):
        return self._processes() if self._processes else process_state(self.root)

    @staticmethod
    def in_use(path, processes):
        target = str(path)
        return any(target in text for text in processes['paths'])

    def scan(self, usage=True, with_quota=True):
        self.warnings = []
        report = {'home': str(self.home), 'host': socket.gethostname(),
                  'time': self.now(), 'quota': self._quota() if with_quota else {}, 'candidates': [],
                  'protected': [], 'warnings': self.warnings, 'usage': [],
                  'local_commit': self.local_commit, 'server_versions': []}
        if not self.root.exists() and not self.root.is_symlink():
            report['warnings'].append('账户中没有 VS Code 远程组件，可查看下方目录占用。')
            if usage:
                report['usage'] = self.home_usage()
            return report
        if self.root.is_symlink() or not self.root.is_dir():
            raise SafetyError('远程组件目录不是普通目录，已停止清理。')
        if uid() is not None and self.root.stat().st_uid != uid():
            raise SafetyError('远程组件目录不属于当前账户。')
        processes = self.proc()
        report['active_processes'] = processes['count']
        if processes['uncertain']:
            self.warnings.append('无法完整检查使用状态；本次只展示占用，不提供清理项。')

        def protect(path, reason):
            report['protected'].append({'path': path.relative_to(self.home).as_posix(), 'reason': reason})

        def candidate(path, kind, title, reason, age=MIN_AGE):
            try:
                self.anchored(path)
                if processes['uncertain'] or self.in_use(path, processes):
                    protect(path, '正在使用，或无法确认使用状态')
                    return
                if self.now() - path.lstat().st_mtime < age:
                    protect(path, '最近仍有变动，稍后再检查')
                    return
                stamp, size, newest = self.fingerprint(path)
                if self.now() - newest < age:
                    protect(path, '目录内仍有近期变动的文件，稍后再检查')
                    return
                if size:
                    report['candidates'].append({'path': path.relative_to(self.home).as_posix(),
                        'kind': kind, 'title': title, 'reason': reason,
                        'bytes': size, 'stamp': stamp})
            except (OSError, SafetyError) as exc:
                protect(path, str(exc))

        valid = []
        for parent, modern in [(self.root/'cli/servers', True), (self.root/'bin', False)]:
            if not parent.exists():
                continue
            try:
                self.anchored(parent)
                for path in sorted(parent.iterdir()):
                    match = SERVER.fullmatch(path.name) if modern else COMMIT.fullmatch(path.name)
                    if not match:
                        continue
                    commit = match[1] if modern else path.name
                    try:
                        base = path/'server' if modern else path
                        product = self.config(base/'product.json')
                        self.anchored(base/'bin/code-server')
                        self.anchored(base/'node')
                        version = product.get('version', '')
                        numbers = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', version)
                        if product.get('commit') != commit or not numbers:
                            raise SafetyError('版本信息不完整')
                        valid.append({'path': path, 'commit': commit, 'version': version,
                                      'sort': tuple(map(int, numbers.groups()))})
                    except (OSError, ValueError, SafetyError):
                        protect(path, '版本信息不完整，保留以免误删')
            except (OSError, SafetyError) as exc:
                self.warnings.append(str(exc))
        keep = {self.local_commit} if self.local_commit else set()
        if valid:
            keep.add(max(valid, key=lambda row: row['sort'])['commit'])
        for row in valid:
            if self.in_use(row['path'], processes):
                keep.add(row['commit'])
        for row in valid:
            report['server_versions'].append({k: row[k] for k in ['commit', 'version']})
            if row['commit'] in keep:
                protect(row['path'], '保留当前、最新或正在运行的版本')
            else:
                candidate(row['path'], 'server', '旧版 VS Code '+row['version'], '已有更新的完整版本')

        # Launchers are only removed if a complete replacement server exists.
        if valid:
            for path in sorted(self.root.iterdir()):
                match = re.fullmatch(r'code-([0-9a-f]{40})', path.name)
                if match and match[1] not in keep and path.is_file():
                    candidate(path, 'launcher', '旧版启动程序', '已被当前版本替代')

        try:
            manifest = self.root/'extensions/extensions.json'
            selected = set()
            profiles = self.root/'data/User/profiles'
            manifests = [manifest]
            if profiles.exists():
                self.anchored(profiles)
                for profile in profiles.iterdir():
                    self.anchored(profile)
                    if (profile/'extensions.json').exists():
                        manifests.append(profile/'extensions.json')
            for path in manifests:
                rows = self.config(path)
                if not isinstance(rows, list):
                    raise SafetyError('扩展选择记录格式异常')
                for row in rows:
                    if not isinstance(row, dict):
                        raise SafetyError('扩展选择记录格式异常')
                    name = row.get('relativeLocation')
                    if name is None:
                        location = row.get('location', {}).get('path', '')
                        absolute = Path(location)
                        if absolute.parent != self.root/'extensions':
                            raise SafetyError('无法确认某个扩展的选择记录')
                        name = absolute.name
                    if not EXTENSION.fullmatch(name) or name in ['.', '..']:
                        raise SafetyError('扩展选择记录中存在异常路径')
                    selected.add(name)
            obsolete_path = self.root/'extensions/.obsolete'
            obsolete = self.config(obsolete_path) if obsolete_path.exists() else {}
            if not isinstance(obsolete, dict):
                raise SafetyError('过期扩展记录格式异常')
            for name, flag in obsolete.items():
                if flag is not True or not EXTENSION.fullmatch(name) or name in ['.', '..']:
                    continue
                path = self.root/'extensions'/name
                if not path.exists() and not path.is_symlink():
                    continue
                if name in selected:
                    protect(path, '仍被当前配置或其他配置选用')
                elif processes['count']:
                    protect(path, '远程 VS Code 仍在运行，暂不清理扩展')
                else:
                    package = self.config(path/'package.json')
                    if not isinstance(package, dict) or not package.get('version'):
                        raise SafetyError('扩展信息不完整')
                    candidate(path, 'extension', '过期扩展 '+name, 'VS Code 已标记过期，且没有配置使用它')
        except (OSError, ValueError, TypeError, SafetyError) as exc:
            # Malformed metadata invalidates all extension deletion decisions.
            report['candidates'] = [r for r in report['candidates'] if r['kind'] != 'extension']
            self.warnings.append('扩展记录无法完整核对，已保留所有扩展：'+str(exc))

        for relative, kind, title, age in [
            ('data/CachedExtensionVSIXs', 'cache', '扩展安装包缓存', MIN_AGE),
            ('data/logs', 'log', '七天前的运行日志', LOG_AGE),
        ]:
            parent = self.root/relative
            if not parent.exists():
                continue
            try:
                self.anchored(parent)
                for path in sorted(parent.iterdir()):
                    display = title
                    if kind == 'log' and re.match(r'^\d{8}T\d{6}$', path.name):
                        display = '运行日志 · '+path.name[:4]+'-'+path.name[4:6]+'-'+path.name[6:8]+' '+path.name[9:11]+':'+path.name[11:13]
                    reason = '安装完成后的下载包，可重新下载' if kind == 'cache' else '已超过七天；保留近期运行记录'
                    candidate(path, kind, display, reason, age)
            except (OSError, SafetyError) as exc:
                self.warnings.append(str(exc))
        for path in sorted(self.root.glob('.cli.*.log')):
            match = re.fullmatch(r'\.cli\.([0-9a-f]{40})\.log', path.name)
            if match and match[1] not in keep:
                candidate(path, 'log', '旧版启动日志', '对应旧版本，且已超过七天', LOG_AGE)
        report['candidates'].sort(key=lambda row: row['bytes'], reverse=True)
        report['reclaimable'] = sum(row['bytes'] for row in report['candidates'])
        report['current_server_missing'] = bool(self.local_commit and not any(
            r['commit'] == self.local_commit for r in valid))
        if usage:
            report['usage'] = self.home_usage()
        return report

    def home_usage(self):
        try:
            output = subprocess.run(['du', '-x', '-B1', '--max-depth=1', '--null', str(self.home)],
                                    capture_output=True, timeout=100).stdout
            rows = []
            for part in output.split(b'\0'):
                if not part.strip() or b'\t' not in part:
                    continue
                size, path = part.split(b'\t', 1)
                path = Path(os.fsdecode(path))
                if path != self.home:
                    rows.append({'name': path.name, 'bytes': int(size),
                                 'managed': path == self.root})
            return sorted(rows, key=lambda row: row['bytes'], reverse=True)[:15]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.warnings.append('目录占用暂时无法统计；不会影响已完成的配额检查。')
            return []

    @contextlib.contextmanager
    def cleanup_lock(self):
        if fcntl is None:
            yield  # Used only by isolated Windows tests; remote main requires Linux.
            return
        path = Path(tempfile.gettempdir())/('cse-space-helper-'+str(uid())+'.lock')
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != uid() or not stat.S_ISREG(info.st_mode):
                raise SafetyError('无法取得清理锁。')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def server_lock(self, row):
        path = self.home/row['path']
        if fcntl is None or row['kind'] not in ['server', 'launcher']:
            yield
            return
        match = re.search(r'([0-9a-f]{40})$', path.name)
        lockdir = self.root/'cli/servers/.locks'
        if not match or not lockdir.is_dir():
            yield
            return
        self.anchored(lockdir)
        lockpath = lockdir/('Stable-'+match[1])
        # Avoid needing free home quota merely to clean an old server.
        if not lockpath.exists():
            yield
            return
        self.anchored(lockpath)
        descriptor = os.open(lockpath, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def clean(self, requested):
        if not isinstance(requested, list) or len(requested) > 3000:
            raise SafetyError('清理选择无效，请重新扫描。')
        removed, skipped, errors = [], [], []
        before = self._quota()
        with self.cleanup_lock():
            fresh = self.scan(usage=False)
            eligible = {row['path']: row for row in fresh['candidates']}
            for wanted in requested:
                if not isinstance(wanted, dict):
                    raise SafetyError('清理选择格式无效。')
                relative = wanted.get('path', '')
                row = eligible.pop(relative, None)
                if row is None or row['stamp'] != wanted.get('stamp'):
                    skipped.append({'path': relative, 'reason': '状态已改变或不属于可清理项，请重新扫描'})
                    continue
                try:
                    path = self.anchored(self.home/relative)
                    with self.server_lock(row):
                        state = self.proc()
                        if state['uncertain'] or self.in_use(path, state) or (
                            row['kind'] == 'extension' and state['count']):
                            raise SafetyError('文件目前正在使用，已跳过')
                        # Profile and version metadata may change even when target bytes do not.
                        allowed = {r['path']: r for r in self.scan(usage=False, with_quota=False)['candidates']}
                        if relative not in allowed or allowed[relative]['stamp'] != row['stamp']:
                            raise SafetyError('扫描后内容或版本选择发生变化，已跳过')
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink()
                    removed.append(row)
                except (OSError, SafetyError) as exc:
                    errors.append({'path': relative, 'reason': str(exc)})
            # Only remove metadata entries for objects successfully removed.
            for rel, transform in [
                ('cli/servers/lru.json', lambda data: [name for name in data if name not in {
                    Path(r['path']).name for r in removed if r['kind'] == 'server'}]),
                ('extensions/.obsolete', lambda data: {name: flag for name, flag in data.items() if name not in {
                    Path(r['path']).name for r in removed if r['kind'] == 'extension'}}),
            ]:
                path = self.root/rel
                if removed and path.exists():
                    try:
                        original = self.config(path)
                        if (rel.endswith('lru.json') and not isinstance(original, list)) or (rel.endswith('.obsolete') and not isinstance(original, dict)):
                            raise SafetyError('版本索引格式异常，已保留原文件')
                        updated = transform(original)
                        if original != updated:
                            temp = path.with_name(path.name+'.cse-helper-'+str(os.getpid()))
                            descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                            try:
                                with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                                    json.dump(updated, output)
                                    output.flush()
                                    os.fsync(output.fileno())
                                temp.replace(path)
                            finally:
                                if temp.exists():
                                    temp.unlink()
                    except (OSError, ValueError, TypeError, SafetyError) as exc:
                        errors.append({'path': str(path.relative_to(self.home)), 'reason': '文件已清理，版本索引更新失败：'+str(exc)})
            after = self.scan()
            writable = False
            try:
                with tempfile.NamedTemporaryFile(prefix='.cse-space-check-', dir=self.home) as probe:
                    probe.write(b'CSE quota write check\n'*512)
                    probe.flush()
                    os.fsync(probe.fileno())
                writable = True
            except OSError:
                pass
        after['cleanup'] = {'before': before, 'removed': removed, 'skipped': skipped,
                            'errors': errors, 'write_ok': writable,
                            'estimated_removed': sum(row['bytes'] for row in removed)}
        return after


def main():
    if not sys.platform.startswith('linux'):
        raise SafetyError('远程检查仅支持 CSE Linux 账户。')
    import pwd
    request = json.load(sys.stdin)
    account = pwd.getpwuid(os.getuid())
    if account.pw_name != request.get('username'):
        raise SafetyError('登录账户与目标账户不一致。')
    engine = Engine(account.pw_dir, request.get('local_commit', ''))
    if request.get('action') == 'scan':
        return engine.scan()
    if request.get('action') == 'clean':
        return engine.clean(request.get('items'))
    raise SafetyError('未知操作。')


if __name__ == '__main__':
    try:
        print(json.dumps({'ok': True, 'report': main()}, ensure_ascii=True))
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=True))
        sys.exit(1)
