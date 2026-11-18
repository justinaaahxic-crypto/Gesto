# -*- coding: utf-8 -*-
"""Gesto 卸载程序（被打包成 Uninstall.exe，安装时复制到安装目录）。

双击 → 确认 → 删除快捷方式 / 注册表项 / 安装目录（自身延时删除）。
命令行：Uninstall.exe --silent
"""
import ctypes
import os
import shutil
import subprocess
import sys
import winreg

APP_NAME = "Gesto"
APP_TITLE = "Gesto 手势演示控制台"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gesto"
EXE_NAME = "Gesto.exe"

BG, CARD, LINE = "#0D1117", "#161C26", "#26303F"
TXT, SUB, ACC = "#E6EAF0", "#98A2B3", "#ED4C5C"
F_UI = "Microsoft YaHei UI"


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def dpi():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def target_dir():
    """卸载器就在安装目录里，所以安装目录 = 自身所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def desktop_dir():
    return os.path.join(os.path.expanduser("~"), "Desktop")


def start_menu_dir():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs")


def dir_size(path):
    total = 0
    for r, _d, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return total


def run_uninstall(target):
    target = os.path.abspath(target)
    for lnk in (os.path.join(desktop_dir(), "%s.lnk" % APP_NAME),
                os.path.join(start_menu_dir(), APP_NAME, "%s.lnk" % APP_NAME),
                os.path.join(start_menu_dir(), APP_NAME, "使用说明.lnk")):
        try:
            if os.path.exists(lnk):
                os.remove(lnk)
        except Exception:
            pass
    try:
        os.rmdir(os.path.join(start_menu_dir(), APP_NAME))
    except Exception:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
    except Exception:
        pass
    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                   capture_output=True, creationflags=0x08000000)

    # 先自己把能删的都删掉（只留正在运行的 exe 和空目录），
    # 剩下的交给 cmd 延时收尾——这样即使延时命令失效，用户看到的也只是一个空文件夹。
    me = os.path.abspath(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.abspath(__file__)
    for name in os.listdir(target) if os.path.isdir(target) else []:
        p = os.path.join(target, name)
        if os.path.abspath(p) == me:
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except Exception:
            pass

    # ping 而不是 timeout：无控制台环境下 timeout 会直接报错退出
    subprocess.Popen('cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "%s"' % target,
                     creationflags=0x08000000, shell=True)


def main():
    dpi()
    target = target_dir()
    silent = "--silent" in sys.argv

    if silent:
        run_uninstall(target)
        return

    import tkinter as tk
    from tkinter import messagebox

    s = 1.0
    root = tk.Tk()
    s = max(1.0, root.winfo_fpixels("1i") / 96.0)
    P = lambda v: int(v * s)          # noqa: E731
    root.title("卸载 %s" % APP_TITLE)
    root.configure(bg=BG)
    root.resizable(False, False)
    w, h = P(520), P(300)
    root.geometry("%dx%d+%d+%d" % (w, h,
                                   (root.winfo_screenwidth() - w) // 2,
                                   (root.winfo_screenheight() - h) // 3))
    try:
        root.iconbitmap(res("logo.ico"))
    except Exception:
        pass

    box = tk.Frame(root, bg=BG)
    box.pack(fill="both", expand=True, padx=P(26), pady=P(22))
    tk.Label(box, text="卸载 Gesto", bg=BG, fg=TXT,
             font=(F_UI, 15, "bold")).pack(anchor="w")
    size = dir_size(target) / 1e6
    tk.Label(box, text="将删除以下内容：", bg=BG, fg=SUB,
             font=(F_UI, 9)).pack(anchor="w", pady=(P(10), P(4)))
    for line in ("程序目录　%s（约 %.0f MB）" % (target, size),
                 "桌面与开始菜单里的 Gesto 快捷方式",
                 "「已安装的应用」里的卸载记录"):
        tk.Label(box, text="· " + line, bg=BG, fg=SUB, font=(F_UI, 9),
                 justify="left", wraplength=P(440), anchor="w").pack(anchor="w")
    tk.Label(box, text="注意：你的设置与日志在 %LOCALAPPDATA%\\Gesto，"
                       "不会被删除；想彻底清理可手动删除该目录。",
             bg=BG, fg="#667085", font=(F_UI, 8), wraplength=P(440),
             justify="left").pack(anchor="w", pady=(P(12), 0))

    foot = tk.Frame(root, bg=BG)
    foot.pack(fill="x", padx=P(26), pady=(0, P(20)))

    def go():
        if not messagebox.askyesno("确认卸载", "确定要卸载 Gesto 吗？", parent=root):
            return
        run_uninstall(target)
        messagebox.showinfo("已卸载", "Gesto 已移除。\n\n"
                                      "程序目录会在几秒内自动清理干净。", parent=root)
        root.destroy()

    tk.Button(foot, text="卸载", command=go, bg=ACC, fg="#FFFFFF", bd=0,
              relief="flat", font=(F_UI, 10, "bold"), activebackground="#C93C41",
              activeforeground="#FFFFFF", cursor="hand2").pack(
        side="right", ipadx=P(22), ipady=P(7))
    tk.Button(foot, text="取消", command=root.destroy, bg="#1C2330", fg=SUB, bd=0,
              relief="flat", font=(F_UI, 9), activebackground="#262F3F",
              activeforeground=TXT, cursor="hand2").pack(
        side="right", padx=(0, P(10)), ipadx=P(16), ipady=P(7))
    root.mainloop()


if __name__ == "__main__":
    main()
