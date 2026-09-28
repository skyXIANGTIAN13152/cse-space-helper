"""Local SSH transport. Passwords never enter files, arguments, or reports."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time

import paramiko

HOST = 'login.cse.unsw.edu.au'
ACCOUNT = re.compile(r'^z\d{7}$')


def resource(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))/name


def local_vscode():
    roots = []
    for environment, suffix in [('LOCALAPPDATA', 'Programs/Microsoft VS Code'),
                                ('PROGRAMFILES', 'Microsoft VS Code'),
                                ('PROGRAMFILES(X86)', 'Microsoft VS Code')]:
        if os.environ.get(environment):
            roots.append(Path(os.environ[environment])/suffix)
    command = shutil.which('code')
    if command:
        roots.append(Path(command).resolve().parent.parent)
    installations = []
    for root in dict.fromkeys(roots):
        # New Windows VS Code updates put resources in a commit-named folder.
        # Follow the active code.cmd target rather than choosing an old folder.
        try:
            launcher = (root/'bin/code.cmd').read_text(encoding='utf-8-sig')
            match = re.search(r'%~dp0\.\.\\([0-9a-f]{7,40})\\resources\\app\\out\\cli\.js', launcher, re.I)
            if match:
                installations.append(root/match[1])
        except OSError:
            pass
        installations.append(root)
    for root in installations:
        try:
            product = json.loads((root/'resources/app/product.json').read_text(encoding='utf-8'))
            package = json.loads((root/'resources/app/package.json').read_text(encoding='utf-8'))
            commit = product.get('commit', '')
            if re.fullmatch(r'[0-9a-f]{40}', commit):
                return {'commit': commit, 'version': package['version']}
        except (OSError, ValueError, KeyError):
            continue
    return {'commit': '', 'version': '未检测到'}


class Session:
    def __init__(self):
        self.client = None
        self.username = None

    @property
    def connected(self):
        transport = self.client.get_transport() if self.client else None
        return bool(transport and transport.is_active() and transport.is_authenticated())

    def connect(self, username, password):
        if not ACCOUNT.fullmatch(username):
            raise ValueError('请输入有效的 zID，例如 z1234567。')
        self.close()
        client = paramiko.SSHClient()
        known = Path.home()/'.ssh/known_hosts'
        if not known.is_file():
            raise RuntimeError('这台电脑还没有 CSE 服务器的验证记录。请先通过系统 SSH 登录一次，再使用本工具。')
        client.load_system_host_keys(str(known))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            client.connect(HOST, username=username, password=password or None,
                           look_for_keys=not bool(password), allow_agent=not bool(password),
                           timeout=15, auth_timeout=25, banner_timeout=20)
            client.get_transport().set_keepalive(25)
            self.client, self.username = client, username
        except paramiko.AuthenticationException as exc:
            client.close()
            raise RuntimeError('登录未通过，请检查 zID 和学校密码后重试。') from exc
        except paramiko.BadHostKeyException as exc:
            client.close()
            raise RuntimeError('服务器身份与这台电脑的记录不一致，连接已停止。请核实 CSE 的 SSH 配置。') from exc
        except (OSError, paramiko.SSHException) as exc:
            client.close()
            raise RuntimeError('无法连接 CSE。请检查网络；如果是首次连接，请先通过系统 SSH 登录。') from exc
        finally:
            password = None

    def run(self, action, items=None, local=None):
        if not self.connected:
            raise RuntimeError('连接已断开，请重新输入密码并扫描。')
        local = local or local_vscode()
        encoded = base64.b64encode(resource('remote_engine.py').read_bytes()).decode('ascii')
        command = 'python3 -u -c "import base64;exec(compile(base64.b64decode(\''+encoded+"'),'cse_space_helper','exec'))\""
        request = {'username': self.username, 'action': action, 'items': items,
                   'local_commit': local['commit']}
        channel = self.client.get_transport().open_session(timeout=20)
        channel.settimeout(30)
        try:
            channel.exec_command(command)
            channel.sendall(json.dumps(request).encode('utf-8'))
            channel.shutdown_write()
            output, error = bytearray(), bytearray()
            deadline = time.monotonic()+420
            while True:
                if channel.recv_ready():
                    output.extend(channel.recv(65536))
                if channel.recv_stderr_ready():
                    error.extend(channel.recv_stderr(65536))
                if len(output) > 16*1024*1024 or len(error) > 1024*1024:
                    raise RuntimeError('服务器返回内容异常过大，已停止本次操作。')
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                if not self.connected or time.monotonic() > deadline:
                    raise RuntimeError('连接中断或检查超时；若刚才正在清理，请重新扫描确认结果。')
                time.sleep(0.04)
            code = channel.recv_exit_status()
            try:
                response = json.loads(output.decode('utf-8'))
            except (UnicodeError, ValueError) as exc:
                raise RuntimeError('远程检查未正常完成，请重新扫描；服务器需要 Python 3.9 或更新版本。') from exc
            if code != 0 or not response.get('ok'):
                raise RuntimeError(response.get('error', '远程操作失败，请重新扫描。'))
            report = response['report']
            report['username'] = self.username
            report['local_vscode'] = local
            return report
        except (OSError, EOFError, paramiko.SSHException) as exc:
            raise RuntimeError('连接中断；清理可能只完成了一部分，请重新登录并扫描确认。') from exc
        finally:
            channel.close()

    def close(self):
        if self.client:
            self.client.close()
        self.client, self.username = None, None


def format_size(value):
    if value is None:
        return '未知'
    if value >= 1024**3:
        return f'{value/1024**3:.2f} GB'
    if value >= 1024**2:
        return f'{value/1024**2:.1f} MB'
    return f'{value/1024:.0f} KB'


def report_text(report):
    quota = report.get('quota', {})
    lines = ['CSE 空间助手 · 检查报告', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(report['time'])),
             f"账户：{report.get('username', '')}    主机：{report['host']}",
             '已用：'+format_size(quota.get('used'))+' / 配额：'+format_size(quota.get('soft')),
             '可清理：'+format_size(report.get('reclaimable', 0)), '', '可清理项目：']
    for row in report.get('candidates', []):
        lines.append(f"{format_size(row['bytes'])}  {row['title']}\n  {row['path']}\n  {row['reason']}")
    if not report.get('candidates'):
        lines.append('没有符合规则的可清理项目。')
    if 'cleanup' in report:
        clean = report['cleanup']
        lines += ['', '清理结果：', f"已清理 {len(clean['removed'])} 项，估计移除 {format_size(clean['estimated_removed'])}",
                  '文件写入验证：'+('通过' if clean['write_ok'] else '未通过')]
        for key, label in [('removed', '已清理'), ('skipped', '已跳过'), ('errors', '未完成')]:
            for row in clean[key]:
                lines.append(f"{label}：{row['path']}  {row.get('reason', '')}")
    lines += ['', '目录占用：']
    lines += [format_size(row['bytes'])+'  '+row['name'] for row in report.get('usage', [])]
    lines += ['', '保留的项目：']
    lines += [row['path']+'：'+row['reason'] for row in report.get('protected', [])]
    lines += ['', '提示：']+report.get('warnings', [])
    return '\n'.join(lines)+'\n'


def save_report(report):
    parent = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'CSESpaceHelper/reports'
    parent.mkdir(parents=True, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns()%1000000)
    (parent/(name+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    path = parent/(name+'.txt')
    path.write_text(report_text(report), encoding='utf-8-sig')
    return path
