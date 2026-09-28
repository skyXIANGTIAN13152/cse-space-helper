"""Application dialogs with edition-specific buttons on any Windows language."""
import tkinter as tk
from tkinter import ttk


def _show(title, message, parent=None, question=False):
    window = tk.Toplevel(parent)
    window.title(title)
    window.resizable(False, False)
    if parent:
        window.transient(parent)
        try:
            window.iconbitmap(parent.iconbitmap())
        except tk.TclError:
            pass
    result = [False]
    scale = max(1.0, window.winfo_fpixels('1i')/96)
    body = ttk.Frame(window, padding=24)
    body.pack(fill='both', expand=True)
    ttk.Label(body, text=message, wraplength=int(530*scale), justify='left').pack(anchor='w')
    buttons = ttk.Frame(body)
    buttons.pack(fill='x', pady=(20, 0))
    def finish(value=False):
        result[0] = value
        window.destroy()
    if question:
        ttk.Button(buttons, text='取消', command=finish).pack(side='right', padx=(8, 0))
    accept = ttk.Button(buttons, text='继续' if question else '确定', command=lambda: finish(True))
    accept.pack(side='right')
    window.protocol('WM_DELETE_WINDOW', finish)
    window.bind('<Escape>', lambda event: finish())
    window.bind('<Return>', lambda event: finish(True))
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = parent.winfo_rootx()+(parent.winfo_width()-width)//2 if parent else 80
    y = parent.winfo_rooty()+(parent.winfo_height()-height)//2 if parent else 80
    window.geometry(f'+{max(0, x)}+{max(0, y)}')
    window.grab_set()
    accept.focus_set()
    window.wait_window()
    return result[0]


def showinfo(title, message, parent=None):
    return _show(title, message, parent)


def showerror(title, message, parent=None):
    return _show(title, message, parent)


def askyesno(title, message, parent=None):
    return _show(title, message, parent, question=True)
