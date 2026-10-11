"""Application dialogs with edition-specific buttons on any Windows language."""
import ctypes
from ctypes import wintypes
import sys
import tkinter as tk
from tkinter import ttk

BG, INK, MUTED, LINE, ACCENT = '#211E1C', '#F8F0E7', '#C1B3A6', '#655246', '#FFBC66'


def theme_titlebar(window):
    """Tint the native caption when supported; keep the normal system controls."""
    if sys.platform != 'win32':
        return
    try:
        window.update_idletasks()
        user32 = ctypes.WinDLL('user32')
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(window.winfo_id(), 2)
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = wintypes.LONG
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0:
                break
        for attribute, color in ((35, BG), (36, INK)):
            rgb = [int(color[index:index+2], 16) for index in (1, 3, 5)]
            value = wintypes.DWORD(rgb[0] | rgb[1] << 8 | rgb[2] << 16)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError, tk.TclError):
        # Earlier Windows versions still have a fully functional native titlebar.
        pass


def _show(title, message, parent=None, question=False):
    window = tk.Toplevel(parent)
    window.title(title)
    window.configure(bg=BG)
    window.resizable(False, False)
    if parent:
        window.transient(parent)
        try:
            window.iconbitmap(parent.iconbitmap())
        except tk.TclError:
            pass
    result = [False]
    scale = max(1.0, window.winfo_fpixels('1i')/96)
    style = ttk.Style(window)
    style.configure('Dialog.TFrame', background=BG)
    style.configure('Dialog.TLabel', background=BG, foreground=INK, font=('Microsoft YaHei UI', 10))
    body = ttk.Frame(window, style='Dialog.TFrame', padding=round(24*scale))
    body.pack(fill='both', expand=True)
    ttk.Label(body, text=message, style='Dialog.TLabel', wraplength=int(550*scale), justify='left').pack(anchor='w')
    tk.Frame(body, bg=LINE, height=1).pack(fill='x', pady=(round(22*scale), round(16*scale)))
    buttons = ttk.Frame(body, style='Dialog.TFrame')
    buttons.pack(fill='x')
    def finish(value=False):
        result[0] = value
        window.destroy()
    if question:
        ttk.Button(buttons, text='取消', command=finish).pack(side='right', padx=(round(8*scale), 0))
    accept = ttk.Button(buttons, text='继续' if question else '确定', style='Primary.TButton', command=lambda: finish(True))
    accept.pack(side='right')
    window.protocol('WM_DELETE_WINDOW', finish)
    window.bind('<Escape>', lambda event: finish())
    window.bind('<Return>', lambda event: event.widget.invoke() if isinstance(event.widget, ttk.Button) else finish(True))
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    x = parent.winfo_rootx()+(parent.winfo_width()-width)//2 if parent else 80
    y = parent.winfo_rooty()+(parent.winfo_height()-height)//2 if parent else 80
    window.geometry(f'+{max(0, x)}+{max(0, y)}')
    theme_titlebar(window)
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
