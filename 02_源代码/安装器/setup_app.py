# -*- coding: utf-8 -*-
"""Gesto 安装程序（被打包成单文件 exe，内置 payload.zip 与卸载器）。

给完全不懂这个项目的人用：双击 → 选目录 → 安装 → 桌面出现带图标的快捷方式。
无需管理员权限，装到 %LOCALAPPDATA%\\Programs\\Gesto，卸载信息写进 HKCU，
所以在「设置 → 应用 → 已安装的应用」里能看到并卸载。

命令行（自动化测试/静默部署用）：
    Gesto_Setup.exe --silent --target D:\\temp\\Gesto --no-shortcuts
    Gesto_Setup.exe --uninstall --target D:\\temp\\Gesto
"""
import ctypes
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import winreg
import zipfile

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Gesto"
APP_TITLE = "Gesto 手势演示控制台"
VERSION = "1.0.4"
PUBLISHER = "Gesto Project"
EXE_NAME = "Gesto.exe"
UNINST_NAME = "Uninstall.exe"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gesto"

BG, CARD, LINE = "#0D1117", "#161C26", "#26303F"
TXT, SUB, ACC = "#E6EAF0", "#98A2B3", "#5B7CFF"
F_UI = "Microsoft YaHei UI"


def res(name):
    """打包后资源在 _MEIPASS，源码运行时在同目录。"""
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


def default_target():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Programs", "Gesto")


def start_menu_dir():
    return os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                        "Start Menu", "Programs")


def desktop_dir():
    d = os.path.join(os.path.expanduser("~"), "Desktop")
    return d if os.path.isdir(d) else os.path.join(os.environ["USERPROFILE"], "Desktop")


# ---------------- 快捷方式（走 COM，用系统自带脚本宿主，不引第三方库）----------------
PS_MAKE = r"""
$ErrorActionPreference = 'Stop'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut({lnk})
$s.TargetPath = {target}
$s.WorkingDirectory = {workdir}
$s.IconLocation = {icon}
$s.Description = {desc}
$s.Save()
"""


def _q(p):
    """PowerShell 单引号字符串转义。"""
    return "'" + str(p).replace("'", "''") + "'"


def make_shortcut(lnk, target, workdir, icon, desc):
    """建一个 .lnk。失败返回 False（调用方负责提示手动创建）。"""
    try:
        os.makedirs(os.path.dirname(lnk), exist_ok=True)
        script = PS_MAKE.format(lnk=_q(lnk), target=_q(target), workdir=_q(workdir),
                                icon=_q(icon), desc=_q(desc))
        ps1 = os.path.join(os.environ.get("TEMP", "."), "gesto_lnk.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:   # BOM 让中文路径不乱码
            f.write(script)
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", ps1],
            capture_output=True, creationflags=0x08000000)
        return r.returncode == 0 and os.path.exists(lnk)
    except Exception:
        return False


# ---------------- 注册表 ----------------
def register_uninstall(target, size_kb):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            vals = {
                "DisplayName": APP_TITLE,
                "DisplayVersion": VERSION,
                "Publisher": PUBLISHER,
                "InstallLocation": target,
                "DisplayIcon": os.path.join(target, EXE_NAME),
                "UninstallString": '"%s"' % os.path.join(target, UNINST_NAME),
                "QuietUninstallString": '"%s" --silent' % os.path.join(target, UNINST_NAME),
                "InstallDate": time.strftime("%Y%m%d"),
                "EstimatedSize": size_kb,
                "NoModify": 1,
                "NoRepair": 1,
            }
            for name, v in vals.items():
                if isinstance(v, int):
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, v)
                else:
                    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(v))
        return True
    except Exception:
        return False


def unregister():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REG_KEY)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


# ---------------- 安装逻辑 ----------------
def do_install(target, mk_desktop, mk_startmenu, progress=None, cancel=None):
    """解包 + 快捷方式 + 注册表。progress(done, total, text)。

    返回 (ok, msg, notes)：notes 是"装完了但要提醒用户的话"（比如快捷方式没建成）。
    """
    target = os.path.abspath(target)
    notes = []
    zp = res("payload.zip")
    if not os.path.exists(zp):
        return False, "安装包不完整：缺少 payload.zip", notes
    os.makedirs(target, exist_ok=True)

    with zipfile.ZipFile(zp) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        total = len(infos)
        for n, info in enumerate(infos, 1):
            if cancel is not None and cancel.is_set():
                return False, "已取消", notes
            out = os.path.join(target, info.filename)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 256)
            if progress and (n % 25 == 0 or n == total):
                progress(n, total, "正在复制 %d / %d 个文件…" % (n, total))

    # 卸载器：安装包里带了一份，复制到安装目录
    un_src = res("uninstall_stub.exe")
    if os.path.exists(un_src):
        try:
            shutil.copyfile(un_src, os.path.join(target, UNINST_NAME))
        except Exception as e:
            notes.append("卸载器复制失败（%s），可在「设置 → 应用」里手动移除。" % e)
    else:
        notes.append("安装包里没有找到卸载器，卸载需手动删除安装目录。")

    exe = os.path.join(target, EXE_NAME)
    icon = exe
    if mk_desktop:
        progress and progress(total, total, "正在创建桌面快捷方式…")
        if not make_shortcut(os.path.join(desktop_dir(), "%s.lnk" % APP_NAME),
                             exe, target, icon, APP_TITLE):
            notes.append("桌面快捷方式没建成：请右键 Gesto.exe → 发送到 → 桌面快捷方式。")
    if mk_startmenu:
        progress and progress(total, total, "正在创建开始菜单项…")
        sm = os.path.join(start_menu_dir(), APP_NAME)
        if not make_shortcut(os.path.join(sm, "%s.lnk" % APP_NAME),
                             exe, target, icon, APP_TITLE):
            notes.append("开始菜单项没建成，可从安装目录直接运行 Gesto.exe。")
        make_shortcut(os.path.join(sm, "使用说明.lnk"),
                      os.path.join(target, "使用说明.txt"), target, icon,
                      "Gesto 使用说明")

    size_kb = 0
    for r, _d, fs in os.walk(target):
        for f in fs:
            try:
                size_kb += os.path.getsize(os.path.join(r, f)) // 1024
            except OSError:
                pass
    if not register_uninstall(target, size_kb):
        notes.append("卸载信息没写进注册表，「已安装的应用」里可能看不到 Gesto。")
    return True, "", notes


def do_uninstall(target, quiet=False):
    """删除快捷方式、注册表、安装目录（自身所在目录最后用 cmd 延时删）。"""
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
    unregister()

    subprocess.run(["taskkill", "/F", "/IM", EXE_NAME],
                   capture_output=True, creationflags=0x08000000)
    # 自身 exe 就在 target 里，直接删会失败 → 交给 cmd 延时处理
    subprocess.Popen('cmd /c timeout /t 2 >nul & rmdir /s /q "%s"' % target,
                     creationflags=0x08000000, shell=True)
    return True


# ---------------- 界面 ----------------
class Setup(tk.Tk):
    def __init__(self, target=None, desktop=True, startmenu=True):
        super().__init__()
        self.title("%s 安装程序" % APP_TITLE)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.target = tk.StringVar(value=target or default_target())
        self.v_desktop = tk.BooleanVar(value=desktop)
        self.v_menu = tk.BooleanVar(value=startmenu)
        self.v_run = tk.BooleanVar(value=True)
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self._build()
        self._center(560, 470)
        try:
            self.iconbitmap(res("logo.ico"))
        except Exception:
            pass
        self.after(120, self._drain)

    def _center(self, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        s = max(1.0, self.winfo_fpixels("1i") / 96.0)
        w, h = int(w * s), int(h * s)
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))

    def _build(self):
        s = max(1.0, self.winfo_fpixels("1i") / 96.0)
        P = lambda v: int(v * s)          # noqa: E731
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=P(24), pady=(P(22), P(6)))
        try:
            self._logo = tk.PhotoImage(file=res("logo_128.png"))
            tk.Label(top, image=self._logo, bg=BG).pack(side="left")
        except Exception:
            pass
        tb = tk.Frame(top, bg=BG)
        tb.pack(side="left", padx=(P(14), 0))
        tk.Label(tb, text="Gesto 手势演示控制台", bg=BG, fg=TXT,
                 font=(F_UI, 15, "bold")).pack(anchor="w")
        tk.Label(tb, text="版本 %s · 免安装依赖 · 约 382 MB" % VERSION, bg=BG, fg=SUB,
                 font=(F_UI, 9)).pack(anchor="w", pady=(P(3), 0))

        body = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        body.pack(fill="both", expand=True, padx=P(24), pady=P(10))
        inner = tk.Frame(body, bg=CARD)
        inner.pack(fill="both", expand=True, padx=P(16), pady=P(14))

        tk.Label(inner, text="安装位置", bg=CARD, fg=TXT,
                 font=(F_UI, 10, "bold")).pack(anchor="w")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(P(6), P(12)))
        self.e_path = tk.Entry(row, textvariable=self.target, bg="#0F141C", fg=TXT,
                               insertbackground=TXT, bd=0, relief="flat",
                               font=(F_UI, 9), highlightthickness=1,
                               highlightbackground=LINE, highlightcolor=ACC)
        self.e_path.pack(side="left", fill="x", expand=True, ipady=P(5))
        tk.Button(row, text="浏览…", command=self._pick, bg="#1C2330", fg=SUB, bd=0,
                  font=(F_UI, 9), activebackground="#262F3F", activeforeground=TXT,
                  relief="flat", cursor="hand2").pack(side="left", padx=(P(8), 0),
                                                      ipadx=P(8), ipady=P(4))

        tk.Label(inner, text="快捷方式", bg=CARD, fg=TXT,
                 font=(F_UI, 10, "bold")).pack(anchor="w")
        for text, var in (("创建桌面快捷方式", self.v_desktop),
                          ("创建开始菜单项（含使用说明）", self.v_menu),
                          ("安装完成后立即启动 Gesto", self.v_run)):
            tk.Checkbutton(inner, text=text, variable=var, bg=CARD, fg=SUB,
                           selectcolor="#0F141C", activebackground=CARD,
                           activeforeground=TXT, bd=0, highlightthickness=0,
                           font=(F_UI, 9), anchor="w").pack(anchor="w", pady=P(2))

        tk.Label(inner, text="提示：安装不需要管理员权限；"
                             "卸载请到「设置 → 应用 → 已安装的应用」里找 Gesto。",
                 bg=CARD, fg="#667085", font=(F_UI, 8), wraplength=P(470),
                 justify="left").pack(anchor="w", pady=(P(10), 0))

        self.bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.status = tk.Label(self, text="准备就绪", bg=BG, fg=SUB, font=(F_UI, 9))

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=P(24), pady=(P(4), P(18)))
        self.btn = tk.Button(foot, text="开始安装", command=self.start,
                             bg=ACC, fg="#FFFFFF", bd=0, relief="flat",
                             font=(F_UI, 10, "bold"), activebackground="#4A6AEB",
                             activeforeground="#FFFFFF", cursor="hand2")
        self.btn.pack(side="right", ipadx=P(22), ipady=P(7))
        self.btn_quit = tk.Button(foot, text="取消", command=self.destroy, bg="#1C2330",
                                  fg=SUB, bd=0, relief="flat", font=(F_UI, 9),
                                  activebackground="#262F3F", activeforeground=TXT,
                                  cursor="hand2")
        self.btn_quit.pack(side="right", padx=(0, P(10)), ipadx=P(16), ipady=P(7))
        self._body = inner

    def _pick(self):
        d = filedialog.askdirectory(initialdir=os.path.dirname(self.target.get()),
                                    title="选择安装位置")
        if d:
            self.target.set(os.path.join(d, "Gesto"))

    def start(self):
        t = self.target.get().strip()
        if not t:
            messagebox.showwarning("路径为空", "请先选择安装位置。")
            return
        if os.path.isdir(t) and os.listdir(t):
            if not messagebox.askyesno(
                    "目录非空", "该目录里已经有文件：\n%s\n\n继续安装会覆盖同名文件，是否继续？" % t):
                return
        self.btn.configure(state="disabled", text="正在安装…")
        self.btn_quit.configure(state="disabled")
        self.bar.pack(fill="x", padx=int(24 * max(1.0, self.winfo_fpixels("1i") / 96.0)),
                      pady=(0, 4))
        self.status.pack(anchor="w", padx=int(24 * max(1.0, self.winfo_fpixels("1i") / 96.0)))
        self.status.configure(text="正在准备…")
        threading.Thread(target=self._work, args=(t,), daemon=True).start()

    def _work(self, target):
        def prog(done, total, text):
            self.q.put(("prog", (done, total, text)))
        ok, msg, notes = do_install(target, self.v_desktop.get(), self.v_menu.get(),
                                    prog, self.cancel)
        self.q.put(("done", (ok, msg, target, notes)))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "prog":
                    done, total, text = payload
                    self.bar["value"] = done * 100.0 / max(1, total)
                    self.status.configure(text=text)
                else:
                    ok, msg, target, notes = payload
                    self._finish(ok, msg, target, notes)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def _finish(self, ok, msg, target, notes):
        self.bar["value"] = 100
        if not ok:
            self.status.configure(text="安装失败：%s" % msg)
            self.btn.configure(state="normal", text="重试")
            self.btn_quit.configure(state="normal")
            messagebox.showerror("安装失败", msg)
            return
        self.status.configure(text="安装完成")
        self.btn.destroy()
        self.btn_quit.configure(text="完成", command=self._close)
        if self.v_run.get():
            try:
                subprocess.Popen([os.path.join(target, EXE_NAME)], cwd=target)
            except Exception as e:
                notes.append("自动启动失败（%s），请到桌面双击 Gesto 图标。" % e)
        tail = ("\n\n需要注意：\n· " + "\n· ".join(notes)) if notes else ""
        messagebox.showinfo("安装完成",
                            "Gesto 已安装到：\n%s\n\n"
                            "① 桌面（或开始菜单）会有 Gesto 图标，双击即可使用\n"
                            "② 第一次用请先看安装目录里的「使用说明.txt」\n"
                            "③ 想要英文翻译和详细讲解，在「设置」里填自己的大模型 API Key"
                            "%s" % (target, tail))

    def _close(self):
        self.destroy()


def main():
    dpi()
    args = sys.argv[1:]
    silent = "--silent" in args
    target = default_target()
    for i, a in enumerate(args):
        if a == "--target" and i + 1 < len(args):
            target = args[i + 1]

    if "--uninstall" in args:
        do_uninstall(target, quiet=silent)
        return

    no_sc = "--no-shortcuts" in args
    if silent:
        ok, msg, notes = do_install(target, not no_sc, not no_sc)
        print(("OK " if ok else "FAIL ") + target + (" " + msg if msg else ""))
        for n in notes:
            print("  ! " + n)
        return

    app = Setup(target=target)
    app.mainloop()


if __name__ == "__main__":
    main()
