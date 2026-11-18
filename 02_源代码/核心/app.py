# -*- coding: utf-8 -*-
"""Gesto — Gesture Presenter 桌面端。

把 hand-ocr/hand_frame_ocr.py 那套手势引擎包成一个有界面的小软件：
左侧导航 + 右侧「功能模块」卡片行（点行尾的 ➡️ 进入对应页面），首页一键启停引擎。

运行：双击项目根目录 Gesto.bat，或
      .venv\\Scripts\\python.exe gesto/app.py
自检：.venv\\Scripts\\python.exe gesto/app.py --smoke   （截图后自动退出，用来看排版）
"""
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request

import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk

# ---------------- 路径（源码运行 / PyInstaller 冻结 两种形态）----------------
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # onedir 打包后：exe 在 dist/Gesto/，资源在 dist/Gesto/_internal/
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE = getattr(sys, "_MEIPASS", os.path.join(APP_DIR, "_internal"))
    BASE = BUNDLE
    ROOT = BUNDLE                      # 资源统一按 bundle 根目录解析
    CFG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", APP_DIR), "Gesto")
    os.makedirs(CFG_DIR, exist_ok=True)   # 安装目录只读，配置必须放可写处
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(BASE)
    CFG_DIR = BASE

ASSETS = os.path.join(BASE, "assets")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
ENGINE = os.path.join(ROOT, "hand-ocr", "hand_frame_ocr.py")
OCR_CFG = os.path.join(ROOT, "hand-ocr", "config.json")
FRAME_CFG = os.path.join(ROOT, "hand-ocr", "frame.json")

APP_NAME = "Gesto"
APP_SUB = "GESTURE PRESENTER"
VERSION = "1.0.4"

# ---------------- 主题 ----------------
BG = "#0D1117"
SIDE = "#0A0E14"
CARD = "#141A23"
FIELD = "#0F141C"
LINE = "#1F2733"
TXT = "#E6EAF0"
SUB = "#98A2B3"
MUT = "#667085"
ACC = "#5B7CFF"
ACC_D = "#4A6AEB"
ACC2 = "#9E6BFF"
OK = "#2ED47A"
WARN = "#FFB020"
ERR = "#FF5A5F"
BTN2 = "#1C2330"       # 次要按钮
BTN2_H = "#262F3F"

F_UI = "Microsoft YaHei UI"
F_LT = "Segoe UI"
SIDE_W = 208

# ---------------- 高 DPI ----------------
# 关键：不声明 DPI 感知的话，Windows 会把整个窗口按 1.5 倍位图拉伸，
# 字全糊（2560x1600 / 150% 的机器上尤其明显）。声明之后让 Tk 按物理像素自己画，
# 同时把布局尺寸统一乘 SCALE，界面大小不变、但字是矢量渲染出来的。
SCALE = 1.0


def enable_dpi_awareness():
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)     # 系统级 DPI 感知
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def px(v):
    """96dpi 设计稿上的像素 → 当前屏物理像素。"""
    return int(round(v * SCALE))



def font(size=10, bold=False, latin=False):
    return (F_LT if latin else F_UI, size, "bold" if bold else "normal")


# ---------------- 配置 ----------------
DEFAULTS = {
    "llm": {"api_key": "", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"},
    "engine": {"zoom": 1.5, "arm_delay": 5.0, "left_is_next": True,
               "restore": True, "zoom_gesture": True, "side_swap": True},
}


def load_cfg():
    """读 Gesto 自己的配置；第一次运行用引擎现有配置打底，界面显示的就是实际生效值。"""
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        for sec in cfg:
            for k in cfg[sec]:
                if user.get(sec, {}).get(k) not in (None, ""):
                    cfg[sec][k] = user[sec][k]
        return cfg, True
    except Exception:
        pass
    # 首次运行：从 hand-ocr 的配置里继承
    try:
        with open(OCR_CFG, "r", encoding="utf-8") as f:
            llm = json.load(f).get("llm", {})
        for k in ("api_key", "base_url", "model"):
            if llm.get(k):
                cfg["llm"][k] = llm[k]
    except Exception:
        pass
    try:
        with open(FRAME_CFG, "r", encoding="utf-8") as f:
            cfg["engine"]["zoom"] = float(json.load(f).get("zoom", 1.5))
    except Exception:
        pass
    return cfg, False


def save_cfg(cfg):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------- 引擎进程 ----------------
class Engine:
    """子进程跑手势引擎：崩了不影响界面，日志逐行回传到界面。"""

    def __init__(self):
        self.proc = None
        self.q = queue.Queue()
        self.started_at = 0.0
        self.log_path = os.path.join(CFG_DIR, "engine.log")

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, env_extra):
        if self.running:
            return False, "引擎已在运行"
        if FROZEN:
            # 打包后没有 python.exe 可调，改为让本 exe 用 --engine 再起一个自己
            cmd = [sys.executable, "--engine"]
            cwd = APP_DIR
        else:
            if not os.path.exists(ENGINE):
                return False, "找不到手势引擎：%s" % ENGINE
            cmd = [sys.executable, ENGINE]
            cwd = ROOT
        if not os.path.isdir(ASSETS):
            return False, "找不到图标资源目录：%s" % ASSETS
        # windowed 打包后子进程的 sys.stdout 是 None，管道收不到任何 print；
        # 所以改成「引擎自己写日志文件、界面这边 tail 它」——不依赖标准输出。
        try:
            open(self.log_path, "w", encoding="utf-8").close()      # 清空
        except Exception as e:
            return False, "无法写日志文件：%s" % e
        env = os.environ.copy()
        env.update(env_extra)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        env["GESTO_ENGINE_LOG"] = self.log_path
        if FROZEN:
            env["GESTO_CFG_DIR"] = CFG_DIR     # 让引擎把可写文件也放到同一处
        flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=cwd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, env=env,
                creationflags=flags)
        except Exception as e:
            return False, "启动失败：%s" % e
        self.started_at = time.time()
        threading.Thread(target=self._pump, daemon=True).start()
        return True, ""

    def _pump(self):
        """尾随日志文件（等价于 tail -f），进程结束后把剩余内容读完。"""
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                while True:
                    line = f.readline()
                    if line:
                        self.q.put(("line", line.rstrip("\n")))
                        continue
                    if self.proc is None or self.proc.poll() is not None:
                        rest = f.read()
                        for extra in rest.splitlines():
                            self.q.put(("line", extra))
                        break
                    time.sleep(0.08)
        except Exception as e:
            self.q.put(("line", "[UI] 读取引擎日志失败: %s" % e))
        code = self.proc.wait() if self.proc else -1
        self.q.put(("exit", code))

    def stop(self):
        if not self.proc:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                               capture_output=True, creationflags=0x08000000)
            else:
                self.proc.terminate()
        except Exception as e:
            self.q.put(("line", "[UI] 停止失败: %s" % e))
        self.proc = None

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                return out


# ---------------- 小部件 ----------------
def round_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x0 + r, y0, x1 - r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y0 + r, x1, y1 - r, x1, y1 - r, x1, y1, x1 - r, y1,
           x1 - r, y1, x0 + r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
           x0, y1 - r, x0, y0 + r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class Pill(tk.Canvas):
    """圆角主按钮（tk 自带按钮没法做圆角）。"""

    def __init__(self, master, text, cmd, w=132, h=38, bg=ACC, fg="#FFFFFF",
                 hover=ACC_D, parent_bg=BG, size=10, bold=True):
        w, h = px(w), px(h)
        super().__init__(master, width=w, height=h, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._pw, self._ph, self._bg, self._hover = w, h, bg, hover  # 别用 _w：那是 Tk 控件路径名
        self._cmd, self._text, self._fg = cmd, text, fg
        self._size, self._bold = size, bold
        self._enabled = True
        self._face = None
        self._draw()
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._paint(self._hover))
        self.bind("<Leave>", lambda e: self._paint(self._bg))

    def _draw(self):
        self.delete("all")
        self._face = round_rect(self, 1, 1, self._pw - 1, self._ph - 1,
                                (self._ph - 2) / 2, fill=self._bg, outline="")
        self.create_text(self._pw / 2, self._ph / 2 + px(1), text=self._text,
                         fill=self._fg, font=font(self._size, self._bold))

    def _paint(self, color):
        if self._enabled and self._face:
            self.itemconfig(self._face, fill=color)

    def _click(self, _e):
        if self._enabled and self._cmd:
            self._cmd()

    def set_text(self, text, bg=None, hover=None):
        self._text = text
        if bg:
            self._bg, self._hover = bg, hover or bg
        self._draw()

    def set_enabled(self, on):
        self._enabled = on
        self._draw()
        self.itemconfig(self._face, fill=self._bg if on else BTN2)
        self.configure(cursor="hand2" if on else "arrow")


class Tile(tk.Frame):
    """卡片：内容放进 self.body；传了 cmd 就整张可点，悬停描边变亮。"""

    def __init__(self, master, cmd=None, pad=(16, 14)):
        super().__init__(master, bg=CARD, highlightthickness=px(1),
                         highlightbackground=LINE)
        self._cmd = cmd
        self.body = tk.Frame(self, bg=CARD)
        self.body.pack(fill="both", expand=True, padx=px(pad[0]), pady=px(pad[1]))

    def wire(self):
        """子控件都建好后调用：把点击/悬停绑到整棵子树上。"""
        if not self._cmd:
            return
        stack = [self]
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._cmd())
            w.bind("<Enter>", lambda e: self.configure(highlightbackground=ACC))
            w.bind("<Leave>", lambda e: self.configure(highlightbackground=LINE))


class NavItem(tk.Frame):
    def __init__(self, master, key, glyph, text, cmd):
        super().__init__(master, bg=SIDE, cursor="hand2")
        self.key = key
        self._cmd = cmd
        self._bar = tk.Frame(self, width=px(3), bg=SIDE)
        self._bar.pack(side="left", fill="y")
        inner = tk.Frame(self, bg=SIDE)
        inner.pack(side="left", fill="both", expand=True, padx=px(12), pady=px(10))
        self._g = tk.Label(inner, text=glyph, bg=SIDE, fg=MUT, font=font(11),
                           width=2, anchor="w")
        self._g.pack(side="left")
        self._t = tk.Label(inner, text=text, bg=SIDE, fg=SUB, font=font(10),
                           anchor="w")
        self._t.pack(side="left", padx=(px(4), 0))
        self._inner = inner
        for w in (self, inner, self._g, self._t):
            w.bind("<Button-1>", lambda e: self._cmd(self.key))
            w.bind("<Enter>", lambda e: self._hot(True))
            w.bind("<Leave>", lambda e: self._hot(False))

    def _hot(self, on):
        if getattr(self, "_active", False):
            return
        c = "#111722" if on else SIDE
        for w in (self, self._inner):
            w.configure(bg=c)
        for w in (self._g, self._t):
            w.configure(bg=c)

    def set_active(self, on):
        self._active = on
        bg = "#151C28" if on else SIDE
        for w in (self, self._inner):
            w.configure(bg=bg)
        self._g.configure(bg=bg, fg=ACC if on else MUT)
        self._t.configure(bg=bg, fg=TXT if on else SUB,
                          font=font(10, on))
        self._bar.configure(bg=ACC if on else SIDE)


# ---------------- 主程序 ----------------
class App:
    def __init__(self, root):
        self.root = root
        self.cfg, existed = load_cfg()
        self.engine = Engine()
        self.pages = {}
        self.nav = {}
        self.cur = None
        self._imgs = {}
        self._log_tag = "info"

        root.title("%s — %s" % (APP_NAME, APP_SUB.title()))
        root.configure(bg=BG)
        # 按屏幕可用大小开窗（逻辑像素，px() 再换算成物理像素）
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(1180, max(1000, sw // SCALE - 120)), min(762, max(700, sh // SCALE - 130))
        root.minsize(px(1000), px(700))
        try:
            root.iconbitmap(os.path.join(ASSETS, "logo.ico"))
        except Exception:
            pass
        self._center(px(w), px(h))

        self._build_sidebar()
        self._build_main()
        self.show("dash")
        if not existed:
            save_cfg(self.cfg)
        self.root.after(150, self._poll)

    # ---------- 布局 ----------
    def _center(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(0, (sh - h) // 2 - 20)))

    def _img(self, name, size=None):
        """size 传 96dpi 设计稿尺寸，内部按 SCALE 放大，高清屏上才不会糊。"""
        key = (name, size)
        if key in self._imgs:
            return self._imgs[key]
        p = os.path.join(ASSETS, name)
        im = Image.open(p).convert("RGBA")
        if size:
            im = im.resize((px(size[0]), px(size[1])), Image.LANCZOS)
        elif SCALE > 1.001:
            im = im.resize((px(im.width), px(im.height)), Image.LANCZOS)
        ph = ImageTk.PhotoImage(im)
        self._imgs[key] = ph
        return ph

    def _build_sidebar(self):
        side = tk.Frame(self.root, bg=SIDE, width=px(SIDE_W))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        head = tk.Frame(side, bg=SIDE)
        head.pack(fill="x", padx=px(20), pady=(px(24), px(22)))
        tk.Label(head, bg=SIDE,
                 image=self._img("logo.png", (36, 36))).pack(side="left")
        tk.Label(head, text=APP_NAME, bg=SIDE, fg=TXT,
                 font=(F_LT, 17, "bold")).pack(side="left", padx=(px(10), 0))

        items = [("dash", "▦", "控制台"), ("guide", "✦", "手势词典"),
                 ("log", "≡", "运行日志"), ("setting", "⚙", "设置"),
                 ("about", "ⓘ", "关于")]
        for key, glyph, text in items:
            n = NavItem(side, key, glyph, text, self.show)
            n.pack(fill="x", padx=(0, px(10)), pady=px(1))
            self.nav[key] = n

        tk.Label(side, text="v" + VERSION, bg=SIDE, fg="#3A4352",
                 font=(F_LT, 8)).pack(side="bottom", anchor="w",
                                      padx=px(20), pady=px(16))
        tk.Frame(side, bg=LINE, width=px(1)).place(relx=1.0, x=-px(1), y=0,
                                                   relheight=1.0)

    def _build_main(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        hd = tk.Frame(main, bg=BG)
        hd.pack(fill="x", padx=px(32), pady=(px(24), 0))
        tt = tk.Frame(hd, bg=BG)
        tt.pack(side="left")
        self.page_title = tk.Label(tt, text="控制台", bg=BG, fg=TXT,
                                   font=font(16, True))
        self.page_title.pack(anchor="w")
        self.page_sub = tk.Label(tt, text="", bg=BG, fg=MUT, font=font(9))
        self.page_sub.pack(anchor="w", pady=(px(2), 0))

        # 状态胶囊：全界面唯一的引擎状态显示
        self.chip = tk.Canvas(hd, width=px(92), height=px(28), bg=BG,
                              highlightthickness=0, bd=0)
        self.chip.pack(side="right")
        self._chip_bg = round_rect(self.chip, px(1), px(1), px(91), px(27), px(13),
                                   fill=CARD, outline="")
        self._chip_dot = self.chip.create_oval(px(14), px(10), px(22), px(18),
                                               fill=MUT, outline="")
        self._chip_txt = self.chip.create_text(px(54), px(14), text="未启动", fill=SUB,
                                              font=font(9))

        self.body = tk.Frame(main, bg=BG)
        self.body.pack(fill="both", expand=True, padx=px(32), pady=(px(20), px(24)))

        self.pages["dash"] = self._page_dash()
        self.pages["guide"] = self._page_guide()
        self.pages["log"] = self._page_log()
        self.pages["setting"] = self._page_setting()
        self.pages["about"] = self._page_about()

    # ---------- 页面 ----------
    def _render_hero(self, cw):
        """按可用宽度重绘横幅（窗口拉伸时跟着变宽，不溢出）。

        cw 已经是物理像素（Tk 布局出来的宽度），内部偏移才需要 px()。
        """
        cw = max(px(560), int(cw))
        h = px(172)
        if abs(cw - self._hero_w) < px(8) and self._hero_w:
            return
        self._hero_w = cw
        im = Image.open(os.path.join(ASSETS, "hero.png")).convert("RGB").resize(
            (cw, h), Image.LANCZOS)
        mask = Image.new("L", (cw, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, cw - 1, h - 1],
                                               radius=px(16), fill=255)
        base = Image.new("RGB", (cw, h), BG)
        base.paste(im, (0, 0), mask)
        self._imgs["hero_card"] = ImageTk.PhotoImage(base)
        hero = self.hero
        hero.configure(width=cw, height=h)
        hero.delete("all")
        hero.create_image(0, 0, image=self._imgs["hero_card"], anchor="nw")
        hero.create_text(px(36), px(66), text="手势翻页 · 取景读屏", fill="#FFFFFF",
                         font=font(19, True), anchor="w")
        hero.create_text(px(37), px(104),
                         text="单手一挥翻页，双手比框读字，握拳收工。全部本地推理。",
                         fill="#B8C4DA", font=font(10), anchor="w")
        hero.create_window(cw - px(36), h // 2, window=self.btn_run, anchor="e")

    def _page_dash(self):
        f = tk.Frame(self.body, bg=BG)
        self.hero = tk.Canvas(f, width=px(828), height=px(172), bg=BG,
                              highlightthickness=0, bd=0)
        self.hero.pack(anchor="w", fill="x")

        self.btn_run = Pill(self.hero, "启动引擎", self.toggle_engine, w=128, h=40,
                            parent_bg="#141B2B")
        self._hero_w = 0
        self._render_hero(self.root.winfo_width() - px(SIDE_W) - px(64))
        f.bind("<Configure>", lambda e: self._render_hero(e.width))

        grid = tk.Frame(f, bg=BG)
        grid.pack(fill="x", pady=(px(20), 0))
        tiles = [
            ("guide", "✦", "手势词典", "左右手分工，七种手势", ACC),
            ("log", "≡", "运行日志", "引擎实时输出", OK),
            ("setting", "⚙", "设置", "大模型与引擎参数", ACC2),
            ("about", "ⓘ", "关于", "上手路径与限制", WARN),
        ]
        for i, (k, g, t, d, color) in enumerate(tiles):
            tile = Tile(grid, cmd=lambda k=k: self.show(k), pad=(16, 16))
            tile.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else px(12), 0))
            tk.Label(tile.body, text=g, bg=CARD, fg=color,
                     font=font(14, True)).pack(anchor="w")
            tk.Label(tile.body, text=t, bg=CARD, fg=TXT,
                     font=font(11, True)).pack(anchor="w", pady=(px(10), 0))
            tk.Label(tile.body, text=d, bg=CARD, fg=MUT,
                     font=font(9)).pack(anchor="w", pady=(px(2), 0))
            tile.wire()
            grid.grid_columnconfigure(i, weight=1, uniform="tile")

        tk.Label(f, text="提示：启动后 5 秒内点一下 PPT 放映画面，模拟按键只发给前台窗口。",
                 bg=BG, fg=MUT, font=font(9)).pack(anchor="w", pady=(px(18), 0))
        return f

    def _page_guide(self):
        f = tk.Frame(self.body, bg=BG)
        # 左右手分工：右手管翻页/关/复原，左手管缩放，双手比框取景
        TEAL = "#5BC8AF"
        cards = [
            ("右掌", "右手·五指张开划", "下一页 / 上一页",
             "掌心朝前、五指全开，横着扫。斜着扫也行，竖直跨度不超过横向的 1.6 倍。", ACC),
            ("右二", "右手·食指+中指划", "连翻（更快）",
             "只伸食指和中指、其余收拢，划一次就是一次翻页，冷却比五指短，适合连翻好几页。", ACC2),
            ("右拳", "右手·握拳", "关面板 / 退放映",
             "面板开着时收面板；没面板时退出放映（Esc）；2.5 秒内再抓一次回桌面。", WARN),
            ("右开", "右手·张开停住", "复原刚关掉的面板",
             "关掉后 6 秒内，把手张开并停住 0.8 秒，那块面板原样回来——类似 Ctrl+Shift+T。", TEAL),
            ("左拳", "左手·握拳", "缩小页面（Ctrl+-）",
             "每握一次缩一档。重复缩放要把手放下再握，不会一直缩到底。", TEAL),
            ("左开", "左手·张开停住", "放大页面（Ctrl+=）",
             "张开放平、停住 0.8 秒放大一档。和右手张开不冲突：系统按左右手分别处理。", TEAL),
        ]
        for i, (g, title, tag, desc, color) in enumerate(cards):
            tile = Tile(f, pad=(16, 14))
            tile.grid(row=i // 2, column=i % 2, sticky="nsew",
                      padx=(0 if i % 2 == 0 else px(12), 0), pady=(0, px(10)))
            top = tk.Frame(tile.body, bg=CARD)
            top.pack(fill="x")
            tk.Label(top, text=g, bg=CARD, fg=color,
                     font=font(10, True)).pack(side="left")
            tk.Label(top, text=title, bg=CARD, fg=TXT,
                     font=font(11, True)).pack(side="left", padx=(px(8), 0))
            tk.Label(top, text=tag, bg=CARD, fg=color,
                     font=font(9)).pack(side="right")
            tk.Label(tile.body, text=desc, bg=CARD, fg=SUB, font=font(9), justify="left",
                     wraplength=px(360), anchor="w").pack(fill="x", pady=(px(8), 0))

        # 最后一张：双手比框，占满一整行
        tile = Tile(f, pad=(16, 14))
        tile.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(0, px(10)))
        top = tk.Frame(tile.body, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="双框", bg=CARD, fg=OK,
                 font=font(10, True)).pack(side="left")
        tk.Label(top, text="双手比取景框", bg=CARD, fg=TXT,
                 font=font(11, True)).pack(side="left", padx=(px(8), 0))
        tk.Label(top, text="识别框内文字", bg=CARD, fg=OK,
                 font=font(9)).pack(side="right")
        tk.Label(tile.body,
                 text="两手各伸食指和中指、把中指收起来（拇指张开），四个指尖围出矩形，稳住 1 秒自动识别。\n"
                      "中指必须收拢，否则会被当成「张开手掌」，和左手的放大手势撞车。",
                 bg=CARD, fg=SUB, font=font(9), justify="left",
                 wraplength=px(780), anchor="w").pack(fill="x", pady=(px(8), 0))
        f.grid_columnconfigure(0, weight=1, uniform="g")
        f.grid_columnconfigure(1, weight=1, uniform="g")

        tk.Label(f, text="判定阈值在 hand-ocr/hand_frame_ocr.py 顶部，改灵敏度直接调常量；"
                         "预览窗左上角会显示 HAND: L/R，可确认左右手没判反。",
                 bg=BG, fg=MUT, font=font(9)).grid(row=4, column=0, columnspan=2,
                                                   sticky="w", pady=(px(4), 0))
        return f

    def _page_log(self):
        f = tk.Frame(self.body, bg=BG)
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x", pady=(0, px(8)))
        tk.Label(bar, text="引擎输出", bg=BG, fg=SUB, font=font(10, True)).pack(side="left")
        Pill(bar, "清空", self._clear_log, w=76, h=30, bg=BTN2, fg=SUB,
             hover=BTN2_H, parent_bg=BG, size=9).pack(side="right")
        Pill(bar, "复制全部", self._copy_log, w=88, h=30, bg=BTN2, fg=SUB,
             hover=BTN2_H, parent_bg=BG, size=9).pack(side="right", padx=(0, px(8)))

        box = tk.Frame(f, bg=CARD, highlightthickness=px(1), highlightbackground=LINE)
        box.pack(fill="both", expand=True)
        self.log = tk.Text(box, bg=FIELD, fg="#C6D2E6", bd=0,
                           insertbackground=TXT, font=(F_LT, 9),
                           padx=px(14), pady=px(12), wrap="word", state="disabled")
        sb = tk.Scrollbar(box, command=self.log.yview, width=px(12),
                          bg=CARD, troughcolor=CARD, bd=0,
                          activebackground=LINE, highlightthickness=0)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        for name, color in (("info", "#C6D2E6"), ("ok", OK), ("warn", WARN),
                            ("err", ERR), ("dim", "#65718A")):
            self.log.tag_configure(name, foreground=color)
        self._log_line("Gesto %s 就绪。点「启动引擎」开始，日志会出现在这里。" % VERSION,
                       "dim")
        return f

    def _page_setting(self):
        f = tk.Frame(self.body, bg=BG)
        left = tk.Frame(f, bg=BG)
        left.pack(fill="both", expand=True)

        def section(title, sub=""):
            tk.Label(left, text=title, bg=BG, fg=TXT,
                     font=font(11, True)).pack(anchor="w", pady=(0, px(2)))
            if sub:
                tk.Label(left, text=sub, bg=BG, fg=MUT,
                         font=font(9)).pack(anchor="w", pady=(0, px(8)))

        def field(label, key, show=None, width=46):
            row = tk.Frame(left, bg=BG)
            row.pack(anchor="w", fill="x", pady=(0, px(8)))
            tk.Label(row, text=label, bg=BG, fg=SUB, font=font(9), width=12,
                     anchor="w").pack(side="left")
            e = tk.Entry(row, bg=FIELD, fg=TXT, insertbackground=TXT, bd=0,
                         relief="flat", font=(F_LT, 10), show=show or "",
                         highlightthickness=px(1), highlightbackground=LINE,
                         highlightcolor=ACC, width=width)
            e.pack(side="left", ipady=px(5), padx=(0, 0))
            e.insert(0, str(self.cfg["llm"].get(key, "")))
            return e

        # —— 大模型 ——
        section("大模型", "留空则纯离线：只能查术语库，做不了英文翻译和详细讲解。")
        preset = tk.Frame(left, bg=BG)
        preset.pack(anchor="w", pady=(0, px(10)))
        for name, url, model in (("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
                                 ("千问", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                  "qwen-plus")):
            Pill(preset, name, lambda u=url, m=model: self._fill_llm(u, m), w=86, h=28,
                 bg=BTN2, fg=SUB, hover=BTN2_H, parent_bg=BG, size=9,
                 bold=False).pack(side="left", padx=(0, px(8)))
        self.e_key = field("API Key", "api_key", show="•")
        self.e_url = field("Base URL", "base_url")
        self.e_model = field("模型名", "model")
        self.btn_test = Pill(left, "测试连通性", self._test_llm, w=110, h=30,
                             bg=BTN2, fg=SUB, hover=BTN2_H, parent_bg=BG, size=9)
        self.btn_test.pack(anchor="w", pady=(px(2), px(4)))
        self.lbl_test = tk.Label(left, text="", bg=BG, fg=MUT, font=font(9))
        self.lbl_test.pack(anchor="w")

        # —— 引擎 ——
        tk.Frame(left, bg=LINE, height=px(1)).pack(fill="x", pady=px(16))
        section("引擎", "改完保存并重启引擎才生效。")
        self.v_zoom = tk.DoubleVar(value=float(self.cfg["engine"]["zoom"]))
        self.v_delay = tk.DoubleVar(value=float(self.cfg["engine"]["arm_delay"]))
        self.v_dir = tk.BooleanVar(value=bool(self.cfg["engine"]["left_is_next"]))
        self.v_restore = tk.BooleanVar(
            value=bool(self.cfg["engine"].get("restore", True)))
        self.v_zoomg = tk.BooleanVar(
            value=bool(self.cfg["engine"].get("zoom_gesture", True)))
        self.v_swap = tk.BooleanVar(
            value=bool(self.cfg["engine"].get("side_swap", True)))

        self._slider(left, "取景框放大", self.v_zoom, 1.0, 2.5, 0.05,
                     "1.0 = 只到屏幕中间一块；1.5 常用；越大越省力但越难停准")
        self._slider(left, "起始倒计时", self.v_delay, 0.0, 15.0, 1.0,
                     "启动后留给你点一下 PPT 放映窗口的时间（秒）")

        drow = tk.Frame(left, bg=BG)
        drow.pack(anchor="w", fill="x", pady=(px(6), 0))
        tk.Label(drow, text="翻页方向", bg=BG, fg=SUB, font=font(9), width=12,
                 anchor="w").pack(side="left")
        for txt, val in (("左滑 = 下一页", True), ("左滑 = 上一页", False)):
            r = tk.Radiobutton(drow, text=txt, value=val, variable=self.v_dir,
                               bg=BG, fg=SUB, activebackground=BG,
                               activeforeground=TXT, selectcolor=FIELD,
                               font=font(9), bd=0, highlightthickness=0)
            r.pack(side="left", padx=(0, px(14)))

        # 复原手势开关：握拳关掉面板后，张开手掌静止 0.8s 把面板叫回来
        rrow = tk.Frame(left, bg=BG)
        rrow.pack(anchor="w", fill="x", pady=(px(6), 0))
        # 三个开关并排两行，省纵向空间（设置页没有滚动条，超了会被裁掉）
        tk.Checkbutton(rrow, text="「张开手掌静止」复原面板",
                       variable=self.v_restore, bg=BG, fg=SUB, selectcolor=FIELD,
                       activebackground=BG, activeforeground=TXT, font=font(9),
                       bd=0, highlightthickness=0, anchor="w").pack(side="left")
        tk.Checkbutton(rrow, text="左手缩放页面",
                       variable=self.v_zoomg, bg=BG, fg=SUB, selectcolor=FIELD,
                       activebackground=BG, activeforeground=TXT, font=font(9),
                       bd=0, highlightthickness=0, anchor="w").pack(side="left",
                                                                  padx=(px(22), 0))
        tk.Checkbutton(rrow, text="左右手判定互换（默认开）",
                       variable=self.v_swap, bg=BG, fg=SUB, selectcolor=FIELD,
                       activebackground=BG, activeforeground=TXT, font=font(9),
                       bd=0, highlightthickness=0, anchor="w").pack(side="left",
                                                                  padx=(px(22), 0))
        tk.Label(left, text="改完保存并重启引擎；用预览窗左上角 HAND: L/R 核对左右手有没有判反。",
                 bg=BG, fg="#4E5A6E", font=font(8)).pack(anchor="w", pady=(px(1), 0))

        bar = tk.Frame(left, bg=BG)
        bar.pack(anchor="w", pady=(px(12), 0))
        Pill(bar, "保存并应用", self.save_settings, w=118, h=34,
             parent_bg=BG).pack(side="left")
        Pill(bar, "恢复默认", self.reset_settings, w=96, h=34, bg=BTN2,
             fg=SUB, hover=BTN2_H, parent_bg=BG, size=9).pack(side="left",
                                                                padx=(px(10), 0))
        self.lbl_save = tk.Label(left, text="", bg=BG, fg=OK, font=font(9))
        self.lbl_save.pack(anchor="w", pady=(px(8), 0))
        return f

    def _slider(self, parent, label, var, lo, hi, step, hint):
        row = tk.Frame(parent, bg=BG)
        row.pack(anchor="w", fill="x", pady=(0, px(2)))
        tk.Label(row, text=label, bg=BG, fg=SUB, font=font(9), width=12,
                 anchor="w").pack(side="left")
        val = tk.Label(row, text="", bg=BG, fg=TXT, font=(F_LT, 10, "bold"), width=5,
                       anchor="w")
        val.pack(side="left")
        sc = tk.Scale(row, from_=lo, to=hi, resolution=step, orient="horizontal",
                      variable=var, bg=BG, fg=TXT, troughcolor=BTN2_H,
                      activebackground=ACC, highlightthickness=0, bd=0,
                      sliderrelief="flat", sliderlength=px(16), width=px(7),
                      showvalue=0, length=px(240),
                      command=lambda v: val.configure(text=("%.2f" % float(v))
                                                      if step < 1 else ("%.0f 秒" % float(v))))
        sc.pack(side="left", padx=(px(4), 0))
        val.configure(text=("%.2f" % var.get()) if step < 1
                      else ("%.0f 秒" % var.get()))
        tk.Label(parent, text=hint, bg=BG, fg="#4E5A6E",
                 font=font(8)).pack(anchor="w", pady=(0, px(10)))
        return sc

    def _page_about(self):
        f = tk.Frame(self.body, bg=BG)
        top = tk.Frame(f, bg=BG)
        top.pack(anchor="w", pady=(0, px(16)))
        tk.Label(top, bg=BG,
                 image=self._img("logo.png", (84, 84))).pack(side="left")
        box = tk.Frame(top, bg=BG)
        box.pack(side="left", padx=(px(16), 0))
        tk.Label(box, bg=BG, image=self._img("wordmark.png")).pack(anchor="w")
        tk.Label(box, text="v%s  ·  本地手势引擎 + 离线 OCR + 大模型讲解" % VERSION,
                 bg=BG, fg=MUT, font=font(9)).pack(anchor="w", pady=(px(6), 0))

        def block(title, lines):
            tk.Label(f, text=title, bg=BG, fg=TXT,
                     font=font(10, True)).pack(anchor="w", pady=(px(12), px(4)))
            for ln in lines:
                row = tk.Frame(f, bg=BG)
                row.pack(anchor="w", fill="x", pady=px(1))
                tk.Label(row, text="·", bg=BG, fg=ACC, font=font(9)).pack(side="left")
                tk.Label(row, text=ln, bg=BG, fg=SUB, font=font(9),
                         justify="left").pack(side="left", padx=(px(6), 0))

        block("最短上手路径", [
            "在「设置」里填 API Key（不填也能跑，只是没有翻译和详细讲解）",
            "回「控制台」点「启动引擎」，5 秒内点一下 PPT 放映窗口",
            "单手左右划翻页；两手比框、稳住 1 秒读屏；握拳收面板 / 退放映",
        ])
        block("技术栈", [
            "MediaPipe HandLandmarker —— 单手 21 关键点，双手机位",
            "RapidOCR (onnxruntime) —— 离线中英文识别，不联网",
            "Tkinter 透明置顶浮层 —— 取景框和结果面板直接画在桌面上",
            "OpenAI 兼容接口 —— 千问 / DeepSeek 任一，负责译文与讲解",
        ])
        block("已知限制", [
            "模拟按键只发给前台窗口，PPT 必须是当前焦点",
            "摄像头同一时间只能被一个程序占用，多开实例会黑屏",
            "取景框放大后同一手势对应的屏幕位移变大，会稍微难停准",
        ])
        return f

    # ---------- 页面切换 ----------
    def show(self, key):
        if key not in self.pages:
            return
        if self.cur == key:
            return
        for k, p in self.pages.items():
            p.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.cur = key
        titles = {"dash": ("控制台", "点行尾 ➡️ 进入模块"),
                  "guide": ("手势词典", "左右手分工、七种手势怎么比"),
                  "log": ("运行日志", "引擎在后台说了什么"),
                  "setting": ("设置", "改动保存后需重启引擎"),
                  "about": ("关于", "Gesto 是什么")}
        t, s = titles.get(key, (key, ""))
        self.page_title.configure(text=t)
        self.page_sub.configure(text=s)
        for k, n in self.nav.items():
            n.set_active(k == key)

    # ---------- 引擎控制 ----------
    def toggle_engine(self):
        if self.engine.running:
            self.engine.stop()
            self._log_line("[UI] 已发送停止指令", "warn")
            self._set_state("stopping")
        else:
            env = self._engine_env()
            ok, err = self.engine.start(env)
            if not ok:
                messagebox.showerror("启动失败", err, parent=self.root)
                return
            self._log_line("[UI] 引擎已启动 PID=%s" % self.engine.proc.pid, "ok")
            self._log_line("[UI] 环境：" + "  ".join("%s=%s" % kv for kv in env.items()),
                           "dim")
            self.show("log")
            self._set_state("running")

    def _engine_env(self):
        e = {}
        k = str(self.cfg["llm"]["api_key"]).strip()
        if k:
            e["HAND_OCR_LLM_KEY"] = k
        if self.cfg["llm"]["base_url"]:
            e["HAND_OCR_LLM_URL"] = str(self.cfg["llm"]["base_url"]).strip()
        if self.cfg["llm"]["model"]:
            e["HAND_OCR_LLM_MODEL"] = str(self.cfg["llm"]["model"]).strip()
        e["HAND_OCR_ZOOM"] = "%.2f" % float(self.cfg["engine"]["zoom"])
        e["HAND_OCR_ARM_DELAY"] = "%.1f" % float(self.cfg["engine"]["arm_delay"])
        if not self.cfg["engine"]["left_is_next"]:
            e["HAND_OCR_SWIPE_FLIP"] = "1"
        if not self.cfg["engine"].get("restore", True):
            e["HAND_OCR_RESTORE"] = "0"
        if not self.cfg["engine"].get("zoom_gesture", True):
            e["HAND_OCR_ZOOM_GESTURE"] = "0"
        # 摄像头驱动是否已镜像过因机器而异，所以左右手判定做成可交换的
        _sw = "1" if self.cfg["engine"].get("side_swap", True) else "0"
        e["HAND_SIDE_SWAP"] = _sw
        e["HAND_SIDE_INVERT"] = _sw
        return e

    def _set_state(self, state):
        txt, color = {"running": ("运行中", OK), "stopping": ("停止中", WARN),
                      "idle": ("未启动", MUT), "dead": ("已退出", ERR)}.get(
            state, ("未启动", MUT))
        self.chip.itemconfig(self._chip_dot, fill=color)
        self.chip.itemconfig(self._chip_txt, text=txt, fill=color)
        if state == "running":
            self.btn_run.set_text("停止引擎", bg="#E5484D", hover="#C93C41")
        else:
            self.btn_run.set_text("启动引擎", bg=ACC, hover=ACC_D)

    def _poll(self):
        for kind, payload in self.engine.drain():
            if kind == "line":
                self._log_line(payload, self._classify(payload))
            else:
                self._log_line("[UI] 引擎进程结束，退出码 %s" % payload,
                               "err" if payload not in (0, None) else "warn")
                self._set_state("dead" if payload not in (0, None) else "idle")
        self.root.after(150, self._poll)

    @staticmethod
    def _classify(line):
        if "[zoo" in line or "Traceback" in line or "Error" in line or "[err" in line:
            return "err"
        if line.startswith("[llm]") or line.startswith("[ZOOM]"):
            return "dim"
        if line.startswith("[SLIDE]") or line.startswith("[FIST]") or line.startswith("[UI]"):
            return "ok"
        if line.startswith("[warn]"):
            return "warn"
        return "info"

    def _log_line(self, text, tag="info"):
        if not hasattr(self, "log"):
            return
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S  ") + text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get("1.0", "end").strip())
        self.lbl_save.configure(text="日志已复制到剪贴板")

    # ---------- 设置 ----------
    def _fill_llm(self, url, model):
        for e, v in ((self.e_url, url), (self.e_model, model)):
            e.delete(0, "end")
            e.insert(0, v)

    def save_settings(self):
        self.cfg["llm"]["api_key"] = self.e_key.get().strip()
        self.cfg["llm"]["base_url"] = self.e_url.get().strip()
        self.cfg["llm"]["model"] = self.e_model.get().strip()
        self.cfg["engine"]["zoom"] = round(float(self.v_zoom.get()), 2)
        self.cfg["engine"]["arm_delay"] = round(float(self.v_delay.get()), 1)
        self.cfg["engine"]["left_is_next"] = bool(self.v_dir.get())
        self.cfg["engine"]["restore"] = bool(self.v_restore.get())
        self.cfg["engine"]["zoom_gesture"] = bool(self.v_zoomg.get())
        self.cfg["engine"]["side_swap"] = bool(self.v_swap.get())
        try:
            save_cfg(self.cfg)
        except Exception as e:
            self.lbl_save.configure(text="保存失败：%s" % e, fg=ERR)
            return
        warm = "。引擎正在运行，重启后生效" if self.engine.running else ""
        self.lbl_save.configure(text="已保存" + warm, fg=OK)

    def reset_settings(self):
        self.v_zoom.set(DEFAULTS["engine"]["zoom"])
        self.v_delay.set(DEFAULTS["engine"]["arm_delay"])
        self.v_dir.set(True)
        self.v_restore.set(True)
        self.v_zoomg.set(True)
        self.v_swap.set(True)
        self.lbl_save.configure(text="已恢复默认值（记得点保存）", fg=WARN)

    def _test_llm(self):
        key = self.e_key.get().strip()
        url = self.e_url.get().strip().rstrip("/")
        model = self.e_model.get().strip()
        if not key:
            self.lbl_test.configure(text="没填 API Key：当前是纯离线模式", fg=WARN)
            return
        self.lbl_test.configure(text="测试中…", fg=SUB)

        def work():
            body = json.dumps({"model": model, "max_tokens": 8, "messages": [
                {"role": "user", "content": "ping"}]}).encode("utf-8")
            req = urllib.request.Request(
                url + "/chat/completions", data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=12) as r:
                    json.loads(r.read().decode("utf-8"))
                msg, col = "连通正常（%.1fs）" % (time.time() - t0), OK
            except urllib.error.HTTPError as e:
                msg, col = "HTTP %s：%s" % (e.code, {
                    401: "Key 不对", 403: "账号未开通/未实名",
                    404: "Base URL 或模型名不对", 429: "额度用完"}.get(e.code, "看日志")), ERR
            except Exception as e:
                msg, col = "连不上：%s" % type(e).__name__, ERR
            self.root.after(0, lambda: self.lbl_test.configure(text=msg, fg=col))

        threading.Thread(target=work, daemon=True).start()

    def on_close(self):
        if self.engine.running:
            if not messagebox.askyesno("退出 Gesto", "手势引擎还在运行，退出会一并关掉。继续？",
                                       parent=self.root):
                return
            self.engine.stop()
            time.sleep(0.3)
        self.root.destroy()


def check_layout(root, app):
    """不靠肉眼：逐页把每个控件的实际位置和请求尺寸量一遍，报溢出/裁切。"""
    bad = []

    def walk(w):
        out = [w]
        for c in w.winfo_children():
            if not isinstance(c, (tk.Toplevel, tk.Tk)):
                out.extend(walk(c))
        return out

    for key in ("dash", "guide", "log", "setting", "about"):
        app.show(key)
        root.update()
        page = app.pages[key]
        pw, ph = page.winfo_width(), page.winfo_height()
        if page.winfo_reqheight() > ph + 1:
            bad.append("%-8s 页面内容高 %d > 可见 %d（底部会被裁掉）"
                       % (key, page.winfo_reqheight(), ph))
        for w in walk(page):
            if not w.winfo_ismapped():
                continue
            mw, mh = w.master.winfo_width(), w.master.winfo_height()
            if w.winfo_x() + w.winfo_width() > mw + 2:
                bad.append("%-8s %-12s 右溢出 %d px（x=%d w=%d 父宽=%d）"
                           % (key, w.winfo_class(), w.winfo_x() + w.winfo_width() - mw,
                              w.winfo_x(), w.winfo_width(), mw))
            if isinstance(w, tk.Label) and w.winfo_reqwidth() > w.winfo_width() + 2:
                bad.append("%-8s 文字被裁：%r 需要 %d 实得 %d"
                           % (key, (w.cget("text") or "")[:14],
                              w.winfo_reqwidth(), w.winfo_width()))
            if isinstance(w, (tk.Label, tk.Entry, tk.Button)) and \
                    w.winfo_reqheight() > w.winfo_height() + 2:
                bad.append("%-8s 高度不够：%r 需要 %d 实得 %d（竖着被压扁）"
                           % (key, (w.cget("text") or "")[:14] if hasattr(w, "cget")
                              and "text" in w.keys() else w.winfo_class(),
                              w.winfo_reqheight(), w.winfo_height()))
    return bad


def make_root():
    """DPI 感知必须在 Tk() 之前开，SCALE 必须在任何 px() 之前算出来。"""
    global SCALE
    enable_dpi_awareness()
    root = tk.Tk()
    SCALE = max(1.0, min(2.5, root.winfo_fpixels("1i") / 96.0))
    return root


def smoke():
    """自检：建界面 → 逐页截屏 → 退出。用来肉眼确认排版，不开摄像头。"""
    root = make_root()
    app = App(root)
    print("DPI: %d%%  窗口 %dx%d" % (
        round(SCALE * 100), root.winfo_width(), root.winfo_height()))
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.5)
    issues = check_layout(root, app)
    print("布局检查：%s" % ("全部通过" if not issues else "%d 处问题" % len(issues)))
    for s in issues:
        print("  ! " + s)
    shots = []
    for key in ("dash", "guide", "log", "setting", "about"):
        app.show(key)
        root.update()
        time.sleep(0.25)
        from PIL import ImageGrab
        img = ImageGrab.grab()            # 整屏抓，坐标永远对得上
        p = os.path.join(os.environ.get("TEMP", BASE), "gesto_smoke_%s.png" % key)
        img.save(p)
        shots.append(p)
    root.attributes("-topmost", False)
    root.destroy()
    print("smoke shots:")
    for p in shots:
        print("  " + p)


def run_engine():
    """被 UI 以 `Gesto.exe --engine` 拉起的子进程：只跑手势引擎，不建界面。

    打包后没有 python.exe 可用，所以靠复用自己这个 exe 来起引擎；
    源码运行时为了少一次进程开销，本来也可以直接 import，这里统一走 import。
    """
    if not FROZEN:
        sys.path.insert(0, os.path.join(ROOT, "hand-ocr"))
    # 打包成 GUI 程序后 sys.stdout 是 None，print 会丢；
    # 父进程通过 GESTO_ENGINE_LOG 指定一个日志文件，这里把 stdout 接过去。
    log = os.environ.get("GESTO_ENGINE_LOG")
    if log:
        try:
            f = open(log, "a", encoding="utf-8", buffering=1, errors="replace")
            sys.stdout = f
            sys.stderr = f
        except Exception:
            pass
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    import hand_frame_ocr
    hand_frame_ocr.main()


def main():
    if "--engine" in sys.argv:
        run_engine()
        return
    if "--smoke" in sys.argv:
        smoke()
        return
    root = make_root()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
