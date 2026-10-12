import argparse
import ctypes
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog
import dialogs as messagebox

from client import Session, HOST, local_vscode, format_size, report_text, save_report, resource
from build_info import VERSION, LANGUAGE
from updater import (check_release, download_update, discard_download, launch_update, run_update_helper,
                     confirm_update_startup, UpdateCancelled)

# A quiet, outlined workspace: the terminal shield is the only decorative mark.
BG, CARD, INK, MUTED = '#211E1C', '#2B2521', '#F8F0E7', '#C1B3A6'
LINE, ACCENT, TRACK, CORAL = '#655246', '#FFBC66', '#FFBC66', '#FF806B'
WARNING = '#FFD281'
FONT = 'Microsoft YaHei UI'


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
        self.updating = False
        self.update_cancel = threading.Event()
        self.pending_update_prompt = False
        self.prompted_update_version = None
        self.closing = False
        self.close_when_download_finishes = False
        self.local = local_vscode()
        self.user = tk.StringVar()
        self.password = tk.StringVar()
        self.status = tk.StringVar(value='输入 zID 和学校密码，点击「连接并扫描」。')
        root.title('CSE 空间助手')
        self.scale = max(1.0, root.winfo_fpixels('1i')/96)
        px = self.px
        available_w, available_h = root.winfo_screenwidth()-70, root.winfo_screenheight()-110
        root.geometry(f'{min(px(1120), available_w)}x{min(px(810), available_h)}')
        root.minsize(min(px(950), available_w), min(px(720), available_h))
        root.configure(bg=BG)
        root.protocol('WM_DELETE_WINDOW', self.close)
        try:
            root.iconbitmap(str(resource('app.ico')))
        except tk.TclError:
            pass
        self.style()
        root.after_idle(lambda: messagebox.theme_titlebar(root))
        shell = ttk.Frame(root, style='Page.TFrame', padding=(px(26), px(18)))
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(6, weight=1)
        self.wrap_targets = []
        shell.bind('<Configure>', self.resize_text)

        header = ttk.Frame(shell, style='Page.TFrame')
        header.grid(row=0, column=0, sticky='ew')
        self.brand_mark(header).pack(side='left', padx=(0, px(14)))
        ttk.Label(header, text='CSE 空间助手', style='Title.TLabel').pack(side='left')
        ttk.Button(header, text='使用说明', command=self.help).pack(side='right')
        self.update_button = ttk.Button(header, text='检查更新', command=lambda: self.check_updates(manual=True))
        self.update_button.pack(side='right', padx=(0, px(8)))
        subtitle = ttk.Label(shell, text='扫描服务器占用，清理旧开发组件，让作业有地方保存。', style='Muted.TLabel', wraplength=px(850))
        subtitle.grid(row=1, column=0, sticky='ew', pady=(px(5), px(12)))
        self.wrap_targets.append((subtitle, 0))

        login_outline = tk.Frame(shell, bg=LINE, bd=0)
        login_outline.grid(row=2, column=0, sticky='ew')
        login = ttk.Frame(login_outline, style='Card.TFrame', padding=(px(18), px(12)))
        login.pack(fill='both', expand=True, padx=px(1.5), pady=px(1.5))
        ttk.Label(login, text='学校账户', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(login, text='学校密码  ·  仅用于本次连接', style='Card.TLabel').grid(row=0, column=1, sticky='w', padx=(px(18), 0))
        self.user_entry = ttk.Entry(login, textvariable=self.user, width=17)
        self.user_entry.grid(row=1, column=0, pady=(px(6), 0), sticky='ew')
        self.pass_entry = ttk.Entry(login, textvariable=self.password, show='●', width=25)
        self.pass_entry.grid(row=1, column=1, padx=(px(18), 0), pady=(px(6), 0), sticky='ew')
        self.pass_entry.bind('<Return>', lambda event: self.scan())
        self.scan_button = ttk.Button(login, text='连接并扫描', style='Primary.TButton', command=self.scan)
        self.scan_button.grid(row=1, column=2, padx=(px(18), 0), pady=(px(6), 0), sticky='ns')
        self.disconnect_button = ttk.Button(login, text='断开', command=self.disconnect, state='disabled')
        self.disconnect_button.grid(row=1, column=3, padx=(px(8), 0), pady=(px(6), 0), sticky='ns')
        login.columnconfigure(1, weight=1)
        self.connection_label = ttk.Label(login, text=HOST+'  ·  本机 VS Code '+self.local['version'], style='CardMuted.TLabel')
        self.connection_label.grid(row=2, column=0, columnspan=4, sticky='w', pady=(px(10), 0))

        stats = ttk.Frame(shell, style='Page.TFrame')
        stats.grid(row=3, column=0, sticky='ew', pady=(px(16), px(12)))
        self.used_value = self.stat_measure(stats, '当前已用 / 正常配额', '等待扫描', 0)
        self.free_value = self.stat_measure(stats, '配额剩余', '—', 1)
        self.clean_value = self.stat_measure(stats, '选中可释放（估计）', '—', 2)
        self.progress = ttk.Progressbar(shell, mode='determinate', style='Quota.Horizontal.TProgressbar')
        self.progress.grid(row=4, column=0, sticky='ew', pady=(0, px(9)))
        self.health = ttk.Label(shell, text='扫描会读取当前配额和目录大小。', style='Muted.TLabel', wraplength=px(850))
        self.health.grid(row=5, column=0, sticky='ew', pady=(0, px(12)))
        self.wrap_targets.append((self.health, 0))

        tabs = ttk.Notebook(shell)
        tabs.grid(row=6, column=0, sticky='nsew')
        clean_tab = ttk.Frame(tabs, padding=px(14), style='Card.TFrame')
        usage_tab = ttk.Frame(tabs, padding=px(14), style='Card.TFrame')
        detail_tab = ttk.Frame(tabs, padding=px(14), style='Card.TFrame')
        tabs.add(clean_tab, text='  可清理项目  ')
        tabs.add(usage_tab, text='  目录占用  ')
        tabs.add(detail_tab, text='  检查详情  ')
        clean_note = ttk.Label(clean_tab, text='使用完后，先关闭所有连接此账户的 VS Code 窗口。点击项目可取消勾选。', style='CardMuted.TLabel', wraplength=px(820))
        clean_note.pack(fill='x', pady=(0, px(11)))
        self.wrap_targets.append((clean_note, px(28)))
        self.tree = self.tree_widget(clean_tab, [('check', '选择', 60), ('item', '清理项目', 290), ('size', '占用', 100), ('reason', '清理依据', 405)])
        self.tree.insert('', 'end', iid='empty', values=('', '等待扫描', '', '课程文件、下载文件和编辑历史会保留。'), tags=('quiet',))
        self.tree.bind('<Button-1>', self.toggle)
        self.tree.bind('<space>', self.toggle_keyboard)
        self.tree.bind('<<TreeviewSelect>>', self.describe)
        self.path_label = ttk.Label(clean_tab, text='课程文件、下载文件和编辑历史会保留。', style='CardMuted.TLabel', wraplength=px(820))
        self.path_label.pack(fill='x', pady=(px(10), 0))
        self.wrap_targets.append((self.path_label, px(28)))
        usage_note = ttk.Label(usage_tab, text='这里帮助你了解空间去向。只有「可清理项目」页中的条目能够被本工具清理。', style='CardMuted.TLabel', wraplength=px(820))
        usage_note.pack(fill='x', pady=(0, px(11)))
        self.wrap_targets.append((usage_note, px(28)))
        self.usage_tree = self.tree_widget(usage_tab, [('name', '文件夹', 480), ('size', '占用', 140), ('scope', '处理范围', 235)])
        self.details = tk.Text(detail_tab, wrap='word', relief='flat', bg=CARD, fg=INK,
                               font=(FONT, 10), state='disabled', padx=px(6), pady=px(6),
                               insertbackground=INK, selectbackground='#594034', selectforeground=INK,
                               highlightthickness=1, highlightbackground=CARD, highlightcolor=ACCENT)
        scrollbar = ttk.Scrollbar(detail_tab, orient='vertical', command=self.details.yview)
        scrollbar.pack(side='right', fill='y')
        self.details.configure(yscrollcommand=scrollbar.set)
        self.details.pack(fill='both', expand=True)

        footer = ttk.Frame(shell, style='Page.TFrame')
        footer.grid(row=7, column=0, sticky='ew', pady=(px(12), 0))
        self.select_button = ttk.Button(footer, text='全选 / 取消全选', command=self.select_all, state='disabled')
        self.select_button.pack(side='left')
        self.export_button = ttk.Button(footer, text='导出报告', command=self.export, state='disabled')
        self.export_button.pack(side='left', padx=px(8))
        self.clean_button = ttk.Button(footer, text='清理选中项目', style='Coral.TButton', command=self.clean, state='disabled')
        self.clean_button.pack(side='right')
        self.spinner = ttk.Progressbar(shell, mode='indeterminate', style='Activity.Horizontal.TProgressbar')
        self.spinner.grid(row=8, column=0, sticky='ew', pady=(px(12), 0))
        self.spinner.grid_remove()
        status_label = ttk.Label(shell, textvariable=self.status, style='Muted.TLabel', wraplength=px(850))
        status_label.grid(row=9, column=0, sticky='ew', pady=(px(8), 0))
        self.wrap_targets.append((status_label, 0))
        update_bar = ttk.Frame(shell, style='Page.TFrame')
        update_bar.grid(row=10, column=0, sticky='ew', pady=(px(8), 0))
        self.update_status = tk.StringVar(value='版本 '+VERSION+' · 启动时自动检查更新')
        self.update_label = ttk.Label(update_bar, textvariable=self.update_status, style='Fine.TLabel', wraplength=px(650))
        self.update_label.pack(side='left')
        self.wrap_targets.append((self.update_label, px(320)))
        self.download_button = ttk.Button(update_bar, text='立即更新', command=self.open_update)
        self.cancel_update_button = ttk.Button(update_bar, text='取消下载', command=self.cancel_update)
        self.update_progress = ttk.Progressbar(update_bar, style='Quota.Horizontal.TProgressbar', length=px(140))
        root.after(100, self.poll)
        root.after(1200, self.check_updates)
        root.after(6*60*60*1000, self.periodic_updates)
        self.user_entry.focus_set()
        if initial_report:
            self.render(initial_report, offline=True)

    def px(self, value):
        return max(1, round(value*self.scale))

    def resize_text(self, event):
        # Wrap to the actual content width, including on smaller high-DPI screens.
        content_width = max(self.px(200), event.width-self.px(52))
        for label, inset in self.wrap_targets:
            label.configure(wraplength=max(self.px(180), content_width-inset))

    def brand_mark(self, parent):
        mark = tk.Canvas(parent, width=self.px(56), height=self.px(56), bg=BG,
                         highlightthickness=0, bd=0, takefocus=False)
        unit = 56*self.scale/256
        def line(points, color, width=11):
            mark.create_line(*[coordinate*unit for point in points for coordinate in point],
                             fill=color, width=width*unit, capstyle='round', joinstyle='round')
        def curve(start, first, second, end):
            return [tuple((1-t)**3*start[j]+3*(1-t)**2*t*first[j]+3*(1-t)*t*t*second[j]+t**3*end[j]
                          for j in (0, 1)) for t in [step/20 for step in range(1, 21)]]
        # Same open shield, prompt and sparkle as the transparent desktop icon.
        left = [(165, 39), (128, 26), (40, 57), (40, 123)]
        left += curve((40, 123), (40, 172), (74, 207), (128, 232))
        right = [(128, 232)] + curve((128, 232), (182, 207), (216, 172), (216, 123)) + [(216, 105)]
        line(left, ACCENT)
        line(right, CORAL)
        line([(78, 95), (111, 124), (78, 153)], ACCENT)
        line([(131, 153), (168, 153)], CORAL)
        line([(206, 27), (214, 51), (238, 59), (214, 67), (206, 91),
              (198, 67), (174, 59), (198, 51), (206, 27)], CORAL, 8)
        return mark

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=(FONT, 10), foreground=INK, background=BG,
                        troughcolor=BG, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.configure('TFrame', background=BG)
        style.configure('Page.TFrame', background=BG)
        style.configure('Card.TFrame', background=CARD)
        style.configure('TLabel', background=BG)
        style.configure('Title.TLabel', background=BG, foreground=INK, font=(FONT, 23, 'bold'))
        style.configure('Muted.TLabel', background=BG, foreground=MUTED)
        style.configure('Fine.TLabel', background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure('Card.TLabel', background=CARD, foreground=INK, font=(FONT, 9))
        style.configure('CardMuted.TLabel', background=CARD, foreground=MUTED, font=(FONT, 9))
        style.configure('Measure.TLabel', background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure('Value.TLabel', background=BG, foreground=INK, font=(FONT, 21, 'bold'))
        style.configure('Free.Value.TLabel', foreground=ACCENT)
        style.configure('Clean.Value.TLabel', foreground=CORAL)
        style.configure('TButton', padding=(self.px(13), self.px(8)), background=BG,
                        foreground=INK, borderwidth=self.px(1), relief='solid', focuscolor=ACCENT,
                        focusthickness=1, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map('TButton', background=[('disabled', BG), ('pressed', '#49382E'), ('active', '#3A3029')],
                  foreground=[('disabled', '#A49385')],
                  bordercolor=[('disabled', '#514238'), ('focus', ACCENT), ('active', '#C49270')],
                  lightcolor=[('disabled', '#514238'), ('focus', ACCENT), ('active', '#C49270')],
                  darkcolor=[('disabled', '#514238'), ('focus', ACCENT), ('active', '#C49270')])
        for name, accent in [('Primary.TButton', ACCENT), ('Coral.TButton', CORAL)]:
            style.configure(name, foreground=accent, bordercolor=accent, lightcolor=accent,
                            darkcolor=accent, focuscolor=accent, padding=(self.px(18), self.px(8)))
            style.map(name, background=[('disabled', BG), ('pressed', '#49382E'), ('active', '#3A3029')],
                      foreground=[('disabled', '#A49385'), ('active', INK)],
                      bordercolor=[('disabled', '#514238'), ('focus', INK), ('active', accent)],
                      lightcolor=[('disabled', '#514238'), ('focus', INK), ('active', accent)],
                      darkcolor=[('disabled', '#514238'), ('focus', INK), ('active', accent)])
        style.configure('TEntry', padding=self.px(8), fieldbackground=BG, foreground=INK,
                        insertcolor=INK, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                        selectbackground='#594034', selectforeground=INK)
        style.map('TEntry', fieldbackground=[('disabled', CARD)], foreground=[('disabled', MUTED)],
                  bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
        style.configure('Treeview', rowheight=self.px(33), background=CARD, fieldbackground=CARD,
                        foreground=INK, borderwidth=0, lightcolor=CARD, darkcolor=CARD)
        style.configure('Treeview.Heading', background=BG, foreground=MUTED, relief='flat',
                        padding=(self.px(8), self.px(8)), font=(FONT, 9),
                        bordercolor=LINE, lightcolor=BG, darkcolor=BG)
        style.map('Treeview.Heading', background=[('active', '#3A3029')], foreground=[('active', INK)])
        style.map('Treeview', background=[('selected', '#594034')], foreground=[('selected', INK)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure('TNotebook.Tab', padding=(self.px(12), self.px(9)), background=BG,
                        foreground=MUTED, borderwidth=1, lightcolor=BG, darkcolor=BG,
                        bordercolor=BG, focuscolor=ACCENT)
        style.map('TNotebook.Tab', background=[('selected', CARD), ('active', '#3A3029')],
                  foreground=[('selected', ACCENT), ('active', INK)],
                  bordercolor=[('selected', LINE), ('focus', ACCENT)], lightcolor=[('selected', LINE)])
        style.configure('TScrollbar', background='#655246', troughcolor=CARD, borderwidth=0,
                        arrowcolor=MUTED, arrowsize=self.px(12), lightcolor=CARD, darkcolor=CARD)
        style.map('TScrollbar', background=[('active', '#9C785A'), ('pressed', '#C49270')])
        # Clam needs room for both trough and bar borders; arrowsize=5 gives a 9px meter.
        style.configure('Quota.Horizontal.TProgressbar', background=TRACK, troughcolor='#49392E',
                        borderwidth=0, lightcolor=TRACK, darkcolor=TRACK, arrowsize=self.px(5))
        style.configure('Warning.Quota.Horizontal.TProgressbar', background=WARNING, lightcolor=WARNING, darkcolor=WARNING)
        style.configure('Activity.Horizontal.TProgressbar', background=CORAL, troughcolor=BG,
                        borderwidth=0, lightcolor=CORAL, darkcolor=CORAL, arrowsize=self.px(5))

    def stat_measure(self, parent, label, initial, column):
        if column:
            separator = tk.Frame(parent, bg=LINE, width=self.px(1.5))
            separator.grid(row=0, column=column*2-1, sticky='ns', padx=self.px(21), pady=self.px(2))
        block = ttk.Frame(parent, style='Page.TFrame')
        block.grid(row=0, column=column*2, sticky='nsew')
        parent.columnconfigure(column*2, weight=3 if column == 0 else 2)
        ttk.Label(block, text=label, style='Measure.TLabel').pack(anchor='w')
        value = tk.StringVar(value=initial)
        value_style = ['Value.TLabel', 'Free.Value.TLabel', 'Clean.Value.TLabel'][column]
        ttk.Label(block, textvariable=value, style=value_style).pack(anchor='w', pady=(self.px(6), 0))
        return value

    def tree_widget(self, parent, columns):
        frame = ttk.Frame(parent, style='Card.TFrame')
        frame.pack(fill='both', expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=5)
        for key, label, width in columns:
            tree.heading(key, text=label, anchor='w' if key not in ['check', 'size'] else 'center')
            tree.column(key, width=self.px(width), minwidth=self.px(45 if key == 'check' else 90),
                        stretch=key not in ['check', 'size'], anchor='center' if key in ['check', 'size'] else 'w')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.tag_configure('alternate', background='#302923')
        tree.tag_configure('quiet', foreground=MUTED)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        return tree

    def start(self, operation, callback):
        if self.busy or self.updating:
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
        if self.busy or self.updating:
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
        if self.busy or self.updating or not self.session.connected or not self.selected:
            return
        items = [{'path': self.rows[key]['path'], 'stamp': self.rows[key]['stamp']} for key in self.rows if key in self.selected]
        # The scan list is the review screen; this button executes that exact selection.
        self.start('clean', lambda: self.session.run('clean', items=items, local=self.local))

    def set_buttons(self):
        blocked = self.busy or self.updating
        self.scan_button.configure(state='disabled' if blocked else 'normal',
                                   text='重新扫描' if self.session.connected else '连接并扫描')
        self.user_entry.configure(state='disabled' if blocked or self.session.connected else 'normal')
        self.pass_entry.configure(state='disabled' if blocked or self.session.connected else 'normal')
        self.disconnect_button.configure(state='normal' if self.session.connected and not blocked else 'disabled')
        self.clean_button.configure(state='normal' if self.selected and self.session.connected and not blocked else 'disabled')
        self.select_button.configure(state='normal' if self.rows and not blocked else 'disabled')
        self.export_button.configure(state='normal' if self.report and not blocked else 'disabled')

    def poll(self):
        try:
            while True:
                kind, result, path = self.events.get_nowait()
                if kind in {'update', 'update_error'}:
                    self.finish_update(kind, result, manual=path)
                    continue
                if kind.startswith('download_'):
                    self.finish_download(kind, result)
                    if self.closing:
                        return
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
        if self.pending_update_prompt and not self.busy and not self.updating:
            self.pending_update_prompt = False
            self.open_update()
        self.root.after(100, self.poll)

    def periodic_updates(self):
        self.check_updates()
        self.root.after(6*60*60*1000, self.periodic_updates)

    def check_updates(self, manual=False):
        if self.checking_updates or self.updating:
            return
        self.checking_updates = True
        self.update_button.configure(state='disabled')
        self.download_button.configure(state='disabled')
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
        self.download_button.configure(state='normal')
        if kind == 'update_error':
            self.update_status.set('版本 '+VERSION+' · 更新检查未完成，可稍后重试')
            if manual:
                messagebox.showerror('检查更新', result, parent=self.root)
            return
        self.available_update = result if result['available'] else None
        if self.available_update:
            self.update_status.set('发现新版 '+result['version']+' · 当前版本 '+VERSION)
            self.download_button.pack(side='right')
            if manual or self.prompted_update_version != result['version']:
                self.pending_update_prompt = True
        else:
            self.download_button.pack_forget()
            self.update_status.set('版本 '+VERSION+' · 未发现更新')
            if manual:
                messagebox.showinfo('检查更新', '当前没有比本机更新的正式版本。', parent=self.root)

    def open_update(self):
        if not self.available_update or self.updating or self.checking_updates:
            return
        if self.busy:
            self.pending_update_prompt = True
            return
        self.pending_update_prompt = False
        info = dict(self.available_update)
        self.prompted_update_version = info['version']
        if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
            messagebox.showinfo('软件更新', '自动安装更新仅支持打包后的 Windows 程序。请使用正式发行版。', parent=self.root)
            return
        if not messagebox.askyesno('发现新版本',
                '发现新版 '+info['version']+'，当前版本 '+VERSION+'。\n\n'
                '是否现在更新？确认后会自动下载、替换旧程序并重新启动。\n'
                '下载大小：'+format_size(info['size'])+'。下载期间可取消。\n'
                '更新前会保留旧程序备份。重新启动后需要重新登录 CSE。', parent=self.root,
                accept_text='立即更新', cancel_text='稍后'):
            self.update_status.set('新版 '+info['version']+' 可用 · 可以稍后点击「立即更新」')
            return
        self.updating = True
        self.update_cancel.clear()
        self.update_button.configure(state='disabled')
        self.download_button.pack_forget()
        self.cancel_update_button.configure(state='normal')
        self.cancel_update_button.pack(side='right')
        self.update_progress.configure(value=0)
        self.update_progress.pack(side='right', padx=(self.px(8), self.px(8)))
        self.update_status.set('正在下载新版 '+info['version']+'…')
        self.set_buttons()
        def worker():
            try:
                path = download_update(info, progress=lambda received, total:
                    self.events.put(('download_progress', (received, total), None)), cancel_event=self.update_cancel)
                self.events.put(('download_complete', (path, info), None))
            except UpdateCancelled:
                self.events.put(('download_cancelled', None, None))
            except Exception as exc:
                self.events.put(('download_error', str(exc), None))
        threading.Thread(target=worker, daemon=True).start()

    def cancel_update(self):
        self.update_cancel.set()
        self.cancel_update_button.configure(state='disabled')
        self.update_status.set('正在取消下载…')

    def finish_download(self, kind, result):
        if kind == 'download_progress':
            received, total = result
            self.update_progress.configure(value=received/total*100 if total else 0)
            if not self.update_cancel.is_set():
                self.update_status.set('正在下载更新：'+format_size(received)+' / '+format_size(total))
            return
        if kind == 'download_complete':
            path, info = result
            if self.update_cancel.is_set():
                try:
                    discard_download(path)
                except OSError:
                    pass
                kind = 'download_cancelled'
            else:
                try:
                    self.update_status.set('下载已验证，正在替换程序并重新启动…')
                    self.root.update_idletasks()
                    launch_update(path, info)
                except Exception as exc:
                    try:
                        discard_download(path)
                    except OSError:
                        pass
                    kind, result = 'download_error', str(exc)
                else:
                    self.closing = True
                    self.password.set('')
                    self.session.close()
                    self.root.destroy()
                    return
        self.updating = False
        if self.close_when_download_finishes:
            self.close()
            return
        self.update_progress.pack_forget()
        self.cancel_update_button.pack_forget()
        self.download_button.pack(side='right')
        self.update_button.configure(state='normal')
        self.set_buttons()
        if kind == 'download_cancelled':
            self.update_status.set('更新下载已取消 · 当前程序仍可继续使用')
        else:
            self.update_status.set('更新未完成 · 可以点击「立即更新」重试')
            messagebox.showerror('更新未完成', result, parent=self.root)

    def update_started(self, manifest_path):
        try:
            result = confirm_update_startup(manifest_path)
            self.update_status.set(result['message'])
            if result['status'] != 'updated':
                messagebox.showerror('更新未完成', result['message'], parent=self.root)
        except Exception as exc:
            messagebox.showerror('更新未完成', str(exc), parent=self.root)

    def render(self, report, offline=False):
        self.report = report
        quota = report.get('quota', {})
        used, soft = quota.get('used'), quota.get('soft')
        self.used_value.set(format_size(used)+' / '+format_size(soft))
        self.free_value.set(format_size(max(0, soft-used)) if used is not None and soft else '未知')
        self.progress.configure(value=min(100, used/soft*100) if used is not None and soft else 0,
                                style='Warning.Quota.Horizontal.TProgressbar' if used is not None and soft and used > soft else 'Quota.Horizontal.TProgressbar')
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
        self.health.configure(text=health, foreground=WARNING if used is not None and soft and used > soft else MUTED)
        self.connection_label.configure(text=f"{report.get('username', self.user.get())} · {HOST} · 本机 VS Code {self.local['version']}"+(' · 已连接' if self.session.connected else ' · 历史报告'))
        for tree in [self.tree, self.usage_tree]:
            tree.delete(*tree.get_children())
        self.rows, self.selected = {}, set()
        for index, row in enumerate(report.get('candidates', [])):
            key = str(index)
            self.rows[key] = row
            self.selected.add(key)
            self.tree.insert('', 'end', iid=key, values=('☑', row['title'], format_size(row['bytes']), row['reason']),
                             tags=('alternate',) if index % 2 else ())
        if not self.rows:
            self.tree.insert('', 'end', iid='empty', values=('', '没有符合规则的可清理项目', '', '可在检查详情中查看保留原因'), tags=('quiet',))
        for index, row in enumerate(report.get('usage', [])):
            self.usage_tree.insert('', 'end', values=(row['name'], format_size(row['bytes']), '可扫描旧组件' if row['managed'] else '保留，不会自动删除'),
                                   tags=('alternate',) if index % 2 else ())
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
        if key in self.rows and not self.busy and not self.updating:
            self.selected.symmetric_difference_update({key})
            self.selection_changed()

    def toggle_keyboard(self, event):
        key = self.tree.focus()
        if key in self.rows and not self.busy and not self.updating:
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
        if self.busy or self.updating:
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
        if self.updating:
            if not messagebox.askyesno('正在下载更新', '关闭将取消本次下载，保留当前程序。\n仍要关闭吗？', parent=self.root):
                return
            self.update_cancel.set()
            self.close_when_download_finishes = True
            self.cancel_update_button.configure(state='disabled')
            self.update_status.set('正在取消下载并关闭…')
            return
        if self.busy and self.operation == 'clean':
            if not messagebox.askyesno('清理还在进行', '现在关闭可能只完成部分清理。下次需重新扫描确认。\n仍要关闭吗？', parent=self.root):
                return
        self.password.set('')
        self.session.close()
        self.closing = True
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--open-report')
    parser.add_argument('--scan-json', action='store_true', help='Read username/password JSON from stdin; emit scan report only.')
    parser.add_argument('--clean-json', action='store_true', help='Read credentials and selected scan items from stdin; emit result.')
    parser.add_argument('--apply-update', help=argparse.SUPPRESS)
    parser.add_argument('--update-result', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.apply_update:
        run_update_helper(args.apply_update)
        return
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
    app = App(root, initial)
    if args.update_result:
        root.after_idle(lambda: app.update_started(args.update_result))
    root.mainloop()


if __name__ == '__main__':
    main()
