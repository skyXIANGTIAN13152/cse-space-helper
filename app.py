import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog
import dialogs as messagebox

from client import Session, HOST, local_vscode, format_size, report_text, save_report, resource
from build_info import VERSION, LANGUAGE
from updater import check_release

BG, CARD, INK, MUTED, BLUE, GREEN = '#f3f6fb', '#ffffff', '#182940', '#60748b', '#235dd4', '#168265'


class App:
    def __init__(self, root, initial_report=None):
        self.root = root
        self.session = Session()
        self.events = queue.Queue()
        self.busy = False
        self.operation = ''
        self.report = None
        self.report_path = None
        self.rows = {}
        self.selected = set()
        self.checking_updates = False
        self.available_update = None
        self.local = local_vscode()
        self.user = tk.StringVar()
        self.password = tk.StringVar()
        self.status = tk.StringVar(value='输入 zID 和学校密码，点击「连接并扫描」。')
        root.title('CSE 空间助手')
        scale = max(1.0, root.winfo_fpixels('1i')/96)
        available_w, available_h = root.winfo_screenwidth()-70, root.winfo_screenheight()-110
        root.geometry(f'{min(int(1100*scale), available_w)}x{min(int(790*scale), available_h)}')
        root.minsize(min(int(950*scale), available_w), min(int(710*scale), available_h))
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        try:
            root.iconbitmap(str(resource('app.ico')))
        except tk.TclError:
            pass
        self.style()
        shell = ttk.Frame(root, style='Page.TFrame', padding=(28, 22))
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(6, weight=1)
        header = ttk.Frame(shell, style='Page.TFrame')
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='CSE 空间助手', style='Title.TLabel').pack(side='left')
        ttk.Button(header, text='使用说明', command=self.help).pack(side='right')
        self.update_button = ttk.Button(header, text='检查更新', command=lambda: self.check_updates(manual=True))
        self.update_button.pack(side='right', padx=(0, 8))
        ttk.Label(shell, text='扫描服务器占用，清理旧开发组件，让作业有地方保存。', style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(3, 18))

        login = ttk.Frame(shell, style='Card.TFrame', padding=16)
        login.grid(row=2, column=0, sticky='ew')
        ttk.Label(login, text='学校账户', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(login, text='学校密码  ·  仅用于本次连接', style='Card.TLabel').grid(row=0, column=1, sticky='w', padx=(16, 0))
        self.user_entry = ttk.Entry(login, textvariable=self.user, width=16)
        self.user_entry.grid(row=1, column=0, pady=(6, 0), sticky='ew')
        self.pass_entry = ttk.Entry(login, textvariable=self.password, show='●', width=28)
        self.pass_entry.grid(row=1, column=1, padx=(16, 0), pady=(6, 0), sticky='ew')
        self.pass_entry.bind('<Return>', lambda event: self.scan())
        self.scan_button = ttk.Button(login, text='连接并扫描', style='Primary.TButton', command=self.scan)
        self.scan_button.grid(row=1, column=2, padx=(18, 0), pady=(6, 0))
        self.disconnect_button = ttk.Button(login, text='断开', command=self.disconnect, state='disabled')
        self.disconnect_button.grid(row=1, column=3, padx=(8, 0), pady=(6, 0))
        login.columnconfigure(1, weight=1)
        self.connection_label = ttk.Label(login, text=HOST+'  ·  本机 VS Code '+self.local['version'], style='CardMuted.TLabel')
        self.connection_label.grid(row=2, column=0, columnspan=4, sticky='w', pady=(10, 0))

        stats = ttk.Frame(shell, style='Page.TFrame')
        stats.grid(row=3, column=0, sticky='ew', pady=16)
        self.used_value = self.stat_card(stats, '当前已用 / 正常配额', '等待扫描', 0)
        self.free_value = self.stat_card(stats, '配额剩余', '—', 1)
        self.clean_value = self.stat_card(stats, '选中可释放（估计）', '—', 2)
        self.progress = ttk.Progressbar(shell, mode='determinate', style='Quota.Horizontal.TProgressbar')
        self.progress.grid(row=4, column=0, sticky='ew', pady=(0, 9))
        self.health = ttk.Label(shell, text='扫描会读取当前配额和目录大小。', style='Muted.TLabel', wraplength=1000)
        self.health.grid(row=5, column=0, sticky='w', pady=(0, 12))

        tabs = ttk.Notebook(shell)
        tabs.grid(row=6, column=0, sticky='nsew')
        clean_tab = ttk.Frame(tabs, padding=12, style='Card.TFrame')
        usage_tab = ttk.Frame(tabs, padding=12, style='Card.TFrame')
        detail_tab = ttk.Frame(tabs, padding=12, style='Card.TFrame')
        tabs.add(clean_tab, text='  可清理项目  ')
        tabs.add(usage_tab, text='  目录占用  ')
        tabs.add(detail_tab, text='  检查详情  ')
        ttk.Label(clean_tab, text='使用完后，先关闭所有连接此账户的 VS Code 窗口。点击项目可取消勾选。', style='CardMuted.TLabel').pack(anchor='w', pady=(0, 9))
        self.tree = self.tree_widget(clean_tab, [('check', '选择', 70), ('item', '清理项目', 340), ('size', '占用', 90), ('reason', '清理依据', 350)])
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        self.tree.bind('<<TreeviewSelect>>', self.describe)
        self.path_label = ttk.Label(clean_tab, text='课程文件、下载文件和编辑历史会保留。', style='CardMuted.TLabel', wraplength=970)
        self.path_label.pack(anchor='w', pady=(9, 0))
        ttk.Label(usage_tab, text='这里帮助你了解空间去向。只有「可清理项目」页中的条目能够被本工具清理。', style='CardMuted.TLabel').pack(anchor='w', pady=(0, 9))
        self.usage_tree = self.tree_widget(usage_tab, [('name', '文件夹', 480), ('size', '占用', 140), ('scope', '处理范围', 240)])
        self.details = tk.Text(detail_tab, wrap='word', relief='flat', bg=CARD, fg=INK,
                               font=('Microsoft YaHei UI', 10), state='disabled', padx=6, pady=6)
        scrollbar = ttk.Scrollbar(detail_tab, orient='vertical', command=self.details.yview)
        scrollbar.pack(side='right', fill='y')
        self.details.configure(yscrollcommand=scrollbar.set)
        self.details.pack(fill='both', expand=True)

        footer = ttk.Frame(shell, style='Page.TFrame')
        footer.grid(row=7, column=0, sticky='ew', pady=(14, 0))
        self.select_button = ttk.Button(footer, text='全选 / 取消全选', command=self.select_all, state='disabled')
        self.select_button.pack(side='left')
        self.export_button = ttk.Button(footer, text='导出报告', command=self.export, state='disabled')
        self.export_button.pack(side='left', padx=8)
        self.clean_button = ttk.Button(footer, text='清理选中项目', style='Primary.TButton', command=self.clean, state='disabled')
        self.clean_button.pack(side='right')
        self.spinner = ttk.Progressbar(shell, mode='indeterminate')
        self.spinner.grid(row=8, column=0, sticky='ew', pady=(13, 7))
        self.spinner.grid_remove()
        ttk.Label(shell, textvariable=self.status, style='Muted.TLabel', wraplength=1000).grid(row=9, column=0, sticky='w')
        update_bar = ttk.Frame(shell, style='Page.TFrame')
        update_bar.grid(row=10, column=0, sticky='ew', pady=(8, 0))
        self.update_status = tk.StringVar(value='版本 '+VERSION+' · 启动时自动检查更新')
        ttk.Label(update_bar, textvariable=self.update_status, style='Muted.TLabel', wraplength=800).pack(side='left')
        self.download_button = ttk.Button(update_bar, text='打开更新页面', command=self.open_update)
        root.after(100, self.poll)
        root.after(1200, self.check_updates)
        self.user_entry.focus_set()
        if initial_report:
            self.render(initial_report, offline=True)

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10), foreground=INK)
        style.configure('Page.TFrame', background=BG)
        style.configure('Card.TFrame', background=CARD)
        style.configure('TLabel', background=BG)
        style.configure('Title.TLabel', background=BG, foreground=INK, font=('Microsoft YaHei UI', 24, 'bold'))
        style.configure('Muted.TLabel', background=BG, foreground=MUTED)
        style.configure('Card.TLabel', background=CARD)
        style.configure('CardMuted.TLabel', background=CARD, foreground=MUTED, font=('Microsoft YaHei UI', 9))
        style.configure('Value.TLabel', background=CARD, foreground=INK, font=('Microsoft YaHei UI', 18, 'bold'))
        style.configure('TButton', padding=(12, 8), background='#e7edf6', borderwidth=0)
        style.map('TButton', background=[('active', '#dbe5f5')])
        style.configure('Primary.TButton', background=BLUE, foreground='white', padding=(18, 9))
        style.map('Primary.TButton', background=[('disabled', '#a5b8db'), ('active', '#1848ad')], foreground=[('disabled', '#edf2fb')])
        style.configure('TEntry', padding=7, fieldbackground=CARD)
        style.configure('Treeview', rowheight=32, background=CARD, fieldbackground=CARD, borderwidth=0)
        style.configure('Treeview.Heading', background='#edf2fa', relief='flat', padding=(8, 8), font=('Microsoft YaHei UI', 9, 'bold'))
        style.map('Treeview', background=[('selected', '#e3ecff')], foreground=[('selected', INK)])
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', padding=(8, 9), background='#e7edf6')
        style.map('TNotebook.Tab', background=[('selected', CARD)])
        style.configure('Quota.Horizontal.TProgressbar', background=GREEN, troughcolor='#e1e8f2', borderwidth=0, thickness=7)

    def stat_card(self, parent, label, initial, column):
        card = ttk.Frame(parent, style='Card.TFrame', padding=(16, 12))
        card.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 10, 0))
        parent.columnconfigure(column, weight=1, uniform='stat')
        ttk.Label(card, text=label, style='CardMuted.TLabel').pack(anchor='w')
        value = tk.StringVar(value=initial)
        ttk.Label(card, textvariable=value, style='Value.TLabel').pack(anchor='w', pady=(5, 0))
        return value

    def tree_widget(self, parent, columns):
        frame = ttk.Frame(parent, style='Card.TFrame')
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=7)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=40, stretch=key not in ['check', 'size'])
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def start(self, operation, callback):
        if self.busy:
            return
        self.busy, self.operation = True, operation
        self.spinner.grid()
        self.spinner.start(13)
        self.set_buttons()
        self.status.set('正在连接并扫描，请稍候…' if operation == 'scan' else '正在核对并清理选中项目，完成后会重新检查配额…')
        def worker():
            try:
                result = callback()
                path = None
                try:
                    path = save_report(result)
                except OSError:
                    result.setdefault('warnings', []).append('本地报告未能保存，可使用「导出报告」。')
                self.events.put(('report', result, path))
            except Exception as exc:
                self.events.put(('error', str(exc), None))
        threading.Thread(target=worker, daemon=True).start()

    def scan(self):
        if self.busy:
            return
        username, password = self.user.get().strip(), self.password.get()
        self.password.set('')
        self.local = local_vscode()
        def perform():
            if not self.session.connected or username != self.session.username:
                self.session.connect(username, password)
            return self.session.run('scan', local=self.local)
        self.start('scan', perform)

    def clean(self):
        if self.busy or not self.session.connected or not self.selected:
            return
        items = [{'path': self.rows[key]['path'], 'stamp': self.rows[key]['stamp']} for key in self.rows if key in self.selected]
        # The scan list is the review screen; this button executes that exact selection.
        self.start('clean', lambda: self.session.run('clean', items=items, local=self.local))

    def set_buttons(self):
        self.scan_button.configure(state='disabled' if self.busy else 'normal',
                                   text='重新扫描' if self.session.connected else '连接并扫描')
        self.user_entry.configure(state='disabled' if self.busy or self.session.connected else 'normal')
        self.pass_entry.configure(state='disabled' if self.busy or self.session.connected else 'normal')
        self.disconnect_button.configure(state='normal' if self.session.connected and not self.busy else 'disabled')
        self.clean_button.configure(state='normal' if self.selected and self.session.connected and not self.busy else 'disabled')
        self.select_button.configure(state='normal' if self.rows and not self.busy else 'disabled')
        self.export_button.configure(state='normal' if self.report and not self.busy else 'disabled')

    def poll(self):
        try:
            while True:
                kind, result, path = self.events.get_nowait()
                if kind in {'update', 'update_error'}:
                    self.finish_update(kind, result, manual=path)
                    continue
                self.busy = False
                self.spinner.stop()
                self.spinner.grid_remove()
                if kind == 'report':
                    self.report_path = path
                    self.render(result)
                else:
                    self.status.set(result)
                    self.selected.clear()
                    self.selection_changed()
                    messagebox.showerror('本次操作未完成', result, parent=self.root)
                self.set_buttons()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def check_updates(self, manual=False):
        if self.checking_updates:
            return
        self.checking_updates = True
        self.update_button.configure(state='disabled')
        self.update_status.set('版本 '+VERSION+' · 正在检查更新…')
        def worker():
            try:
                self.events.put(('update', check_release(), manual))
            except Exception as exc:
                self.events.put(('update_error', str(exc), manual))
        threading.Thread(target=worker, daemon=True).start()

    def finish_update(self, kind, result, manual=False):
        self.checking_updates = False
        self.update_button.configure(state='normal')
        if kind == 'update_error':
            self.update_status.set('版本 '+VERSION+' · 更新检查未完成，可稍后重试')
            if manual:
                messagebox.showerror('检查更新', result, parent=self.root)
            return
        self.available_update = result if result['available'] else None
        if self.available_update:
            self.update_status.set('发现新版 '+result['version']+' · 当前版本 '+VERSION)
            self.download_button.pack(side='right')
        else:
            self.download_button.pack_forget()
            self.update_status.set('版本 '+VERSION+' · 未发现更新')
            if manual:
                messagebox.showinfo('检查更新', '当前没有比本机更新的正式版本。', parent=self.root)

    def open_update(self):
        if self.available_update:
            webbrowser.open(self.available_update['page_url'])

    def render(self, report, offline=False):
        self.report = report
        quota = report.get('quota', {})
        used, soft = quota.get('used'), quota.get('soft')
        self.used_value.set(format_size(used)+' / '+format_size(soft))
        self.free_value.set(format_size(max(0, soft-used)) if used is not None and soft else '未知')
        self.progress.configure(value=min(100, used/soft*100) if used is not None and soft else 0)
        if used is None or not soft:
            health = '配额暂时无法确认，可查看目录占用和检查详情。'
        elif used > soft:
            health = '当前超额 '+format_size(used-soft)+'。先清理下列项目，再检查剩余空间。'
        else:
            health = '配额正常，剩余 '+format_size(soft-used)+'。'+('本次没有可清理项。' if not report.get('candidates') else '可以继续清理下面的旧组件。')
        if report.get('current_server_missing'):
            health += ' 下次连接 VS Code 时，会自动安装与本机匹配的远程组件。'
        if offline:
            health = '上次扫描（'+time.strftime('%m-%d %H:%M', time.localtime(report['time']))+'）：'+health
        self.health.configure(text=health)
        self.connection_label.configure(text=f"{report.get('username', self.user.get())} · {HOST} · 本机 VS Code {self.local['version']}"+(' · 已连接' if self.session.connected else ' · 历史报告'))
        for tree in [self.tree, self.usage_tree]:
            tree.delete(*tree.get_children())
        self.rows, self.selected = {}, set()
        for index, row in enumerate(report.get('candidates', [])):
            key = str(index)
            self.rows[key] = row
            self.selected.add(key)
            self.tree.insert('', 'end', iid=key, values=('☑', row['title'], format_size(row['bytes']), row['reason']))
        if not self.rows:
            self.tree.insert('', 'end', iid='empty', values=('', '没有符合规则的可清理项目', '', '可在检查详情中查看保留原因'))
        for row in report.get('usage', []):
            self.usage_tree.insert('', 'end', values=(row['name'], format_size(row['bytes']), '可扫描旧组件' if row['managed'] else '保留，不会自动删除'))
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', report_text(report))
        self.details.configure(state='disabled')
        self.selection_changed()
        if 'cleanup' in report:
            result = report['cleanup']
            text = f"已清理 {len(result['removed'])} 项，估计释放 {format_size(result['estimated_removed'])}。"
            text += ' 文件写入验证通过。' if result['write_ok'] else ' 文件写入验证未通过，请查看详情。'
            if result['errors'] or result['skipped']:
                text += f" {len(result['errors'])+len(result['skipped'])} 项未清理，详见检查详情。"
            self.status.set(text)
        else:
            self.status.set(('已载入上次报告；连接并扫描后才能清理。' if offline else f"扫描完成，找到 {len(self.rows)} 项可清理内容。") + (' 有保留提示，可在检查详情查看。' if report.get('warnings') else ''))
        self.set_buttons()

    def toggle(self, event):
        key = self.tree.identify_row(event.y)
        if key in self.rows and not self.busy:
            self.selected.symmetric_difference_update({key})
            self.selection_changed()

    def toggle_keyboard(self, event):
        key = self.tree.focus()
        if key in self.rows and not self.busy:
            self.selected.symmetric_difference_update({key})
            self.selection_changed()
        return 'break'

    def selection_changed(self):
        for key in self.rows:
            self.tree.set(key, 'check', '☑' if key in self.selected else '☐')
        self.clean_value.set(format_size(sum(self.rows[k]['bytes'] for k in self.selected)))
        self.set_buttons()

    def describe(self, event=None):
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            self.path_label.configure(text=self.rows[selected[0]]['path'])

    def select_all(self):
        self.selected = set() if len(self.selected) == len(self.rows) else set(self.rows)
        self.selection_changed()

    def disconnect(self):
        if self.busy:
            return
        self.session.close()
        self.password.set('')
        self.status.set('已断开。下次输入密码即可重新扫描。')
        self.connection_label.configure(text=HOST+' · 已断开')
        self.set_buttons()

    def export(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title='保存检查报告', defaultextension='.txt',
                                          initialfile='CSE空间检查-'+time.strftime('%Y%m%d-%H%M')+'.txt',
                                          filetypes=[('文本报告', '*.txt')])
        if path:
            try:
                Path(path).write_text(report_text(self.report), encoding='utf-8-sig')
                self.status.set('报告已保存：'+path)
            except OSError as exc:
                messagebox.showerror('保存失败', str(exc), parent=self.root)

    def help(self):
        messagebox.showinfo('CSE 空间助手 · 使用说明',
            '1. 用完后关闭连接此账户的所有 VS Code 窗口。\n'
            '2. 输入 zID 和学校密码，点击「连接并扫描」。\n'
            '3. 查看项目，按需取消勾选，再点击「清理选中项目」。\n\n'
            '只处理旧远程程序、明确过期的扩展、安装包缓存和旧日志。\n'
            '当前/最新程序、课程文件、下载文件和编辑历史都会保留。\n'
            '近期变动或无法确认的内容会跳过，可下次再扫描。\n\n'
            '密码不保存。清理项目会直接移除；开发组件可重新安装。\n'
            '学校目录会跨机器共享，请关闭其他电脑上连接此账户的 VS Code。\n'
            '工具不会后台运行、定时清理或关闭你的工作进程。\n\n版本 '+VERSION, parent=self.root)

    def close(self):
        if self.busy and self.operation == 'clean':
            if not messagebox.askyesno('清理还在进行', '现在关闭可能只完成部分清理。下次需重新扫描确认。\n仍要关闭吗？', parent=self.root):
                return
        self.password.set('')
        self.session.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--open-report')
    parser.add_argument('--scan-json', action='store_true', help='Read username/password JSON from stdin; emit scan report only.')
    parser.add_argument('--clean-json', action='store_true', help='Read credentials and selected scan items from stdin; emit result.')
    args = parser.parse_args()
    if args.scan_json or args.clean_json:
        request = json.load(sys.stdin)
        session = Session()
        try:
            session.connect(request['username'], request.pop('password', ''))
            report = session.run('clean' if args.clean_json else 'scan', request.get('items'))
            print(json.dumps(report, ensure_ascii=True))
        finally:
            session.close()
        return
    if sys.platform == 'win32':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    initial = None
    if args.open_report:
        try:
            initial = json.loads(Path(args.open_report).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    App(root, initial)
    root.mainloop()


if __name__ == '__main__':
    main()
