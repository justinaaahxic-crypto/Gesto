r"""
手势演示套件 —— 单手翻页 + 双手取景 AI 解读（一套手势同时管两件事）
==================================================================
五条手势，互不冲突：

    单手张开 → 向左划        下一页（→）
    单手张开 → 向右划        上一页（←）
    双手各伸食指+拇指稳住 1s  屏幕上出现青色取景框，识别框内文字 → AI 解读面板
    握拳（面板开着）          关闭面板
    握拳（面板关着）          退出放映（Esc）；2.5 秒内再抓一次 → 回到桌面

为什么这样分派：
    握拳在"翻页"和"解读"两个场景里都被人当成本能动作，所以按上下文分派——
    有面板时握拳是"关掉它"，没面板时是"退出放映"，不需要记两套手势。
    单手划动与双手取景靠"手的数量"天然分开：双手在场时翻页判定直接上锁，
    避免框选过程中手臂一横移就误翻页。

翻页为什么不能只看"往哪划"：
    挥手之后手一定会往回收，而"回收"和"故意往反方向划"在几何上是同一个动作
    （实测录屏：左挥后 0.1 秒手就往回走，位移 0.13，比阈值 0.10 还大），
    所以连挥两次会被读成"左、右、左"。只能靠时序区分，见 SlideTracker 的说明：
    同向要再翻一页必须先回撤复位；反向滑动在翻页后一律吞掉（那必然是手在回位），
    直到手真正停够 0.65 秒才解禁。所以"翻回上一页"＝停一下再反着划。

为什么"框"画在真实屏幕上而不是摄像头预览里：
    摄像头看到的是人，不是屏幕，所以"手上位置 → 屏幕位置"只能按比例映射。
    但只要把矩形实时画在真实屏幕上，你就能看着屏幕把手挪到对的位置——
    映射误差由人眼实时纠正，这比任何标定都稳，演示效果也最好。

运行：
    双击根目录 run_hand_ocr.bat（或在项目根目录跑
    .venv\Scripts\python.exe hand-ocr\hand_frame_ocr.py）

    启动后有 5 秒倒计时，趁这几秒用鼠标点一下 PPT 放映画面——
    模拟按键只发给前台窗口，不点就拿不到焦点，翻页会"看起来没反应"。
    退出：在摄像头预览窗上按 q，或在本控制台按 Ctrl+C。

技术栈（全部预训练模型，无需自己训练）：
    MediaPipe HandLandmarker（2 只手 21 关键点） + RapidOCR（onnxruntime 离线 OCR）
    + Tkinter 透明浮层（置顶 + 鼠标穿透） + pyautogui（模拟按键）
    + 千问/DeepSeek（OpenAI 兼容接口，可选）

大模型（可选，不配则纯离线）：
    首次使用双击根目录「配置大模型.bat」，选厂商 + 粘贴 API Key，会自动验证连通性。
    也可手改 hand-ocr/config.json 的 llm.api_key。
    接入后：框到英文 → 面板先给「中文译文」，再给 200~350 字的中文详细讲解 + 关键词释义；
            框到中文 → 直接给详细讲解。面板右上角标签显示「厂商 · 模型名」。
    没配 Key 时：只能用 glossary.json 做术语对照，整句翻译和详细讲解都没有。

依赖：opencv-python / mediapipe(1.x) / Pillow / rapidocr_onnxruntime / pyautogui
模型：gesture-demo/hand_landmarker.task；释义库：hand-ocr/glossary.json
"""
import collections
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
from PIL import ImageGrab

from mediapipe.tasks.python.vision import HandLandmarker, HandLandmarkerOptions, RunningMode

try:
    import winsound
except ImportError:
    winsound = None

# 翻页/退出放映靠模拟按键，只对前台窗口生效
try:
    import pyautogui
    pyautogui.FAILSAFE = False      # 鼠标甩到屏幕角上也不要抛异常
    pyautogui.PAUSE = 0.0
except Exception:
    pyautogui = None

# ======================= 可调参数 =======================
CAM_INDEX = 0            # 摄像头序号
HOLD_SEC = 1.0           # 取景框需要稳住多久才触发识别（秒）
HOLD_TOL = 0.035         # "稳住"的判定容差：四条边每帧变化不超过画面的 3.5%
MIN_BOX_W = 0.08         # 取景框最小宽度（占画面比例）
MIN_BOX_H = 0.05         # 取景框最小高度。别调太大：想框住单独一行公式时框会比较扁，
                         # 0.05 映射到 1600p 屏幕约 80px 高，够 OCR 认一行字
SMOOTH = 0.35            # 取景框平滑系数（0~1，越大越跟手、越小越稳）
COOLDOWN = 1.0           # 两次识别之间的冷却（秒）

# ===== 取景框的屏幕覆盖倍数 =====
# 摄像头整幅画面本来就 1:1 映射到整屏，但两只手根本张不到摄像头的四个边，
# 所以框最多只能够到屏幕中间一块。放大就是把摄像头中间一小块映射成整屏：
#   zoom=1.0 框的活动范围 = 整幅摄像头画面（老行为，够不到屏幕边缘）
#   zoom=1.5 手张到画面宽度的 2/3 就能框满整屏
#   zoom=2.0 手张到一半就够（更省力，但同样手势对应的屏幕位移翻倍 → 更难停准）
# 运行时在摄像头预览窗按 [ 和 ] 可以 ±0.05 实时调整，会自动记到 hand-ocr/frame.json
DEFAULT_FRAME_ZOOM = 1.5
FRAME_ZOOM_MIN, FRAME_ZOOM_MAX, FRAME_ZOOM_STEP = 1.0, 2.5, 0.05

# ===== 单手滑动翻页（与取景解读共存）=====
ENABLE_SLIDE = True      # 单手左右滑动 → 上一页/下一页
SWIPE_WINDOW = 0.50      # 滑动判定时间窗口（秒）
SWIPE_DIST = 0.09        # 窗口内横向最小位移（占画面比例）。实测真人挥手 0.10~0.60
SWIPE_COOLDOWN = 0.25    # 两次翻页之间的冷却（秒）；连翻模式用 SWIPE_COOLDOWN_FAST
SWIPE_MIN_FINGERS = 3    # 单手至少伸出几指才算"在滑动"：1~2 指是做取景手势，不算
SWIPE_MAX_TILT = 2.0     # 允许的斜挥程度：竖直跨度 <= 横向跨度 * 此值。真人挥手常带
                         # 30~45 度斜角（实测录屏一次 0.21 横 / 0.13 竖），卡太死会漏判
SWIPE_LEFT_IS_NEXT = True   # 向左划 = 下一页，向右划 = 上一页（2026-10-22 确认口径）
                         # 想反过来：改成 False，或启动前设环境变量 HAND_OCR_SWIPE_FLIP=1
SLIDE_LOCK = 0.6         # 双手出现后多久内暂停翻页判定，防止取景结束顺手挥一下就翻页

# 关键：挥手之后手会"回位"，而回位和"故意往反方向滑"在几何上完全是同一个动作
# （实测录屏：左挥后 0.1s 手就往回走，回位位移 0.13 > 阈值 0.10），只能靠时序区分。
# 两道闸：
#   1) 回撤复位：同方向要再翻一页，手必须先往回撤 RESET_DIST——否则一次长挥会连发好几页。
#   2) 反向锁定：翻页后反向滑动先吞掉（它几乎必然是手在回位），
#      直到手真正停够 FLIP_PAUSE 秒才解禁——所以"想翻回上一页"＝停一下再反着挥。
# 这样"连挥两次"＝挥、回位（被吞）、挥，正好两次翻页，中间不需要刻意停顿。
# 2026-10-25 调整：原来要求停稳 0.65 秒才解除反向锁定，导致"左滑完马上右滑"
# 第二下被吞掉（用户报"两次操作被识别成一次"）。逐帧数据里挥手收尾的回位只停
# 0.07~0.10 秒，而人"故意反着挥"时会在折返点停 0.2 秒以上——所以把门槛降到
# FLIP_PAUSE=0.20，既能接住快速的反向操作，又能继续吞掉自然回位。
SWIPE_SETTLE = 0.16      # 【已弃用】旧版靠"停稳 0.16 秒"才解锁，会吞掉入镜即挥的那一下。
                         # 现在改为轨迹建立 4 帧即解锁，常量留着只为兼容旧配置。
STILL_RADIUS = 0.03      # 停稳判据：这段时间内掌心离当前位置不超过此半径（占画面比例）
RESET_DIST = 0.10        # 同方向连发的复位要求：手要往回撤这么多（占画面比例）。
                         # 必须 >= SWIPE_DIST：实测挥手收尾时手心会在终点抖 0.05，
                         # 阈值取 0.05 会让"抖一下再推"被当成第二次挥手，一挥手翻两页。
FLIP_PAUSE = 0.20        # 折返点停多久才算"故意反着挥"（低于此值视为手在回位，吞掉）
REVERSE_RELEASE = FLIP_PAUSE   # 兼容旧名字
RETURN_TTL = 2.0         # 反向锁定的最长持续时间（秒），保险丝
SPEED_WIN = 0.72         # 轨迹缓存长度（秒）：必须大于 FLIP_PAUSE，否则判不出"停够"

# 环境变量可以临时翻转翻页方向（不想改代码时用）
if os.environ.get("HAND_OCR_SWIPE_FLIP", "").strip() in ("1", "true", "TRUE", "yes"):
    SWIPE_LEFT_IS_NEXT = not SWIPE_LEFT_IS_NEXT

# ===== 握拳的多义分派 =====
ENABLE_EXIT_PPT = True   # 面板关着时，握拳 = 退出放映（Esc）；连抓两次 = 回桌面
FIST_RESET = 2.5         # 两次握拳间隔小于此值算"连抓"
ARM_DELAY = 5.0          # 启动倒计时：趁这几秒点一下 PPT 放映窗口拿焦点（按键只发前台）
# Gesto 桌面端可以通过环境变量覆盖，不必改这个文件
try:
    if os.environ.get("HAND_OCR_ARM_DELAY"):
        ARM_DELAY = min(30.0, max(0.0, float(os.environ["HAND_OCR_ARM_DELAY"])))
except ValueError:
    pass


INDEX_RATIO = 1.10       # 食指伸出判定：指尖-手腕 / 食指根-手腕 距离比
FIST_MAX_RATIO = 0.95    # 四指比值全部小于此值才算握拳（实测真握拳 0.63~0.83）
FIST_CONFIRM = 5         # 握拳需连续几帧确认（防止抖动误关）
MIN_PALM = 0.03          # 手掌尺寸小于此值视为噪点，本帧丢弃

# ===== 左右手分工 =====
# 右手：翻页 / 关页面 / 复原（原本的全部功能）
# 左手：缩小（握拳）/ 放大（张开手掌静止）
#
# 关于 Left/Right 到底要不要交换（2026-10-25 实测修正）：
#   MediaPipe 的 handedness 假设输入画面**是镜像的**（自拍视角）。本程序在检测前做了
#   cv2.flip，理论上正好满足该前提、标签即用户真实左右手。
#   但实测（用户右手做翻页动作）系统把它判成了左手 → 说明这台机器的摄像头驱动**自己已经镜像过**，
#   再 flip 一次等于把镜像抵消掉，MediaPipe 反而按"非镜像"解读，于是左右颠倒。
#   不同摄像头驱动行为不一样，所以这里**默认交换**，并提供开关：
#     - 环境变量 HAND_SIDE_SWAP=0 → 不交换（摄像头不镜像的机器用这个）
#     - 桌面端设置里的「左右手判定互换」勾选框
#   预览窗左上角常驻 HAND: L/R，抬手看一眼就能确认对不对。
SIDE_SWAP = os.environ.get("HAND_SIDE_SWAP", "1").strip().lower() not in (
    "0", "false", "no", "off")
# 普适性关键：翻页/关闭/复原只认"画面里的那一只手"，不再要求它是右手。
# 左右手标签只在开启缩放时才用得上——标签判错也不会让翻页失效。
ANY_HAND_PAGING = os.environ.get("HAND_OCR_ANY_HAND", "1").strip().lower() not in (
    "0", "false", "no", "off")
# 左手缩放默认关闭：它依赖左右手标签，且和"任何手都能翻页"天然抢动作。
# 需要时在 Gesto 设置里打开（或 HAND_OCR_ZOOM_GESTURE=1）。
ENABLE_LEFT_ZOOM = os.environ.get("HAND_OCR_ZOOM_GESTURE", "1").strip().lower() in (
    "1", "true", "yes", "on")
ZOOM_MINUS_KEY = ("ctrl", "-")      # 左手握拳 → 缩小页面（浏览器 Ctrl+减号）
ZOOM_PLUS_KEY = ("ctrl", "=")       # 左手张开 → 放大页面（浏览器 Ctrl+等号＝Ctrl+加号）
ZOOM_COOLDOWN = 0.45                # 连续缩放的最小间隔（秒）

# ===== 连翻模式：食指+中指（其余手指收拢）=====
# 五指张开的滑动 = 单次翻页；食指+中指的滑动 = 连翻（冷却更短）。
TWO_FINGER_INDEX = 1.10    # 食指/中指伸出阈值
TWO_FINGER_FOLD = 1.05     # 无名指/小指平均比值低于此值算收拢
SWIPE_COOLDOWN_FAST = 0.15  # 连翻模式下两次翻页的最小间隔（五指模式用 SWIPE_COOLDOWN）

# ===== 普适性：几何阈值按手掌尺寸归一化 =====
# 原来 SWIPE_DIST=0.09 是"占画面比例"的绝对值：手离镜头近的人一动就翻页，
# 手小 / 坐得远的人怎么挥都不动，不同人就得重调。改成"位移 / 手掌宽"的比例后，
# 与人、与距离、与机位都无关。实测手掌宽度在画面里约 0.10~0.20，所以
# 0.75 倍手掌宽 ≈ 0.075~0.15，与原阈值同一量级，但对小手更宽容。
SWIPE_DIST_PALM = 0.75   # 横向位移 ≥ 0.75 个手掌宽，才算一次挥动
SWIPE_DIST_PALM_FAST = 0.55   # 连翻模式（食指+中指）门槛更低：快速连点两下也能各算一次
SWIPE_WINDOW_FAST = 0.32     # 连翻模式的判定窗口更短：否则两次快速滑动会被并成一段轨迹，
                             # span 只跨过门槛一次 → 两下滑动只翻一页（用户实测）
RESET_DIST_PALM = 0.90   # 同向连发前的回撤 ≥ 0.90 个手掌宽
PALM_MIN_THR, PALM_MAX_THR = 0.045, 0.22   # 归一化后的阈值夹在这个区间（防极端手型）
MIN_PALM_SWIPE = 0.055   # 手掌在画面里小于这个宽度就不判翻页：手太远/太小，
                         # 关键点本身就不稳，归一化再准也会被噪声带跑（录屏里
                         # "手刚入画"那两帧就是这样误触发过一次）
STILL_RADIUS_PALM = 0.55 # 停稳半径 = 0.55 个手掌宽（自适应）

# ===== 缩手 / 回位（问题 8）=====
LOST_GRACE = 0.0         # 手真的离开画面后，回来多久内不判动作（0=关闭）
                         # 实测：开着它会吞掉正常的连续挥动（录屏重放从 4 次掉到 2 次），
                         # 而回位问题由 POST_ACTION_GUARD + 终点速度已经解决，所以默认关。
LOST_MIN_GAP = 0.10      # 只有离开超过这么久才算"缩手"（检测闪断一帧不算）
POST_ACTION_GUARD = 0.28 # 动作后这段时间内，反向动作直接吞掉
                         # （必须覆盖「挥完立刻缩手」那段，实测约 0.2~0.25s）
ENDPOINT_SPEED = 2.5     # 终点速度超过此值且净位移不足 → 判为"正在回位"
# 取景框要求"食指伸出 + 中指收拢"，与张开手掌（五指全开）区分开，
# 否则左手做放大手势时会和右手一起被误判成取景框。
FRAME_FOLD_MIDDLE = 1.05

# ===== 复原 / 放大共用的"张开手掌静止"判定 =====
# 注意这不是"随便张开手"就触发：要求在 RESTORE_WINDOW 秒内、五指全开且静止 RESTORE_DWELL 秒。
RESTORE_WINDOW = 6.0     # 关掉面板后，多久之内还能复原（秒）
RESTORE_DWELL = 0.8      # 张开手掌需要静止多久（秒）
OPEN_RATIO = 1.12        # 单指"算伸开"的比值门槛（实测张开手 1.16~1.90）
MIN_OPEN_FINGERS = 3     # 四指里有 3 指超过门槛就算"张开手掌"（原来要求 4 指全过，
                         # 小手 / 手指没完全伸直的人会被判成"没张开"，这是普适性差的主因）
FIST_LOCK_AFTER_CLOSE = 1.2   # 关面板后这段时间内不再响应 Esc/Win+D
ENABLE_RESTORE = True
if os.environ.get("HAND_OCR_RESTORE", "").strip().lower() in ("0", "false", "no", "off"):
    ENABLE_RESTORE = False


# 摄像头子区域 → 整屏 的映射范围。默认摄像头整幅画面映射到整个屏幕。
# 若发现框只能够到屏幕中间一小块，把 SRC_* 之外的值往中间收，例如
#   SRC_L, SRC_R = 0.18, 0.82   表示只用摄像头中间 64% 的横向范围映射整屏
SRC_L, SRC_T, SRC_R, SRC_B = 0.0, 0.0, 1.0, 1.0

PREVIEW_W = 460          # 摄像头预览窗宽度（高度按比例算）

# 浮层配色
C_FRAME = "#00E5FF"      # 取景框主色（青）
C_FRAME_DIM = "#0B5A66"  # 取景框细边（暗青）
C_OK = "#00FF9D"         # 进度条（绿）
C_PANEL_BG = "#0D1622"   # 面板底色（深蓝黑）
C_TITLE = "#7DE3FF"
C_BODY = "#F2F7FA"
C_FOOT = "#7C8B99"

TRANSPARENT_KEY = "#010203"   # 这个颜色的像素会被 Windows 抠成全透明
# =======================================================

# 路径：源码运行 / PyInstaller 冻结 / MSIX 安装 三种形态都要能找到资源，
# 而且**可写文件不能放在安装目录**（MSIX 的安装目录是只读的）。
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    ROOT = BASE                      # 打包后资源统一在 bundle 根下
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(BASE)

# Gesto 桌面端会把它自己那份可写目录传进来；单跑本脚本时就是脚本目录
CFG_DIR = os.environ.get("GESTO_CFG_DIR") or BASE
if not os.path.isdir(CFG_DIR):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
    except Exception:
        CFG_DIR = BASE

MODEL = os.path.join(ROOT, "gesture-demo", "hand_landmarker.task")
# 释义库优先用可写目录里的（用户可改），没有就用 bundle 里那份
_GLOSS_CFG = os.path.join(CFG_DIR, "glossary.json")
GLOSSARY = _GLOSS_CFG if os.path.exists(_GLOSS_CFG) else os.path.join(BASE, "glossary.json")

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]

# ==================== 大模型配置（阿里云百炼 · 千问 Qwen）====================
# 走的是 OpenAI 兼容接口，所以换任何兼容厂商（智谱/DeepSeek/Kimi…）都只改 base_url。
# 配置优先级：环境变量 > hand-ocr/config.json > 下面这份内置默认
# 没填 api_key 时程序完全离线，只用 glossary.json 匹配，不会报错也不会联网。
CONFIG_PATH = os.path.join(CFG_DIR, "config.json")
FRAME_CFG = os.path.join(CFG_DIR, "frame.json")  # 只存取景框放大倍数，不碰 config.json

FRAME_ZOOM = DEFAULT_FRAME_ZOOM                  # 运行时可变（按 [ / ] 调整）


def load_frame_zoom():
    # 优先级：环境变量（Gesto 面板传入）> frame.json > 内置默认
    try:
        if os.environ.get("HAND_OCR_ZOOM"):
            z = float(os.environ["HAND_OCR_ZOOM"])
            return min(FRAME_ZOOM_MAX, max(FRAME_ZOOM_MIN, z))
    except ValueError:
        pass
    try:
        with open(FRAME_CFG, "r", encoding="utf-8") as f:
            z = float(json.load(f).get("zoom", DEFAULT_FRAME_ZOOM))
    except Exception:
        return DEFAULT_FRAME_ZOOM
    return min(FRAME_ZOOM_MAX, max(FRAME_ZOOM_MIN, z))


def save_frame_zoom(z):
    try:
        with open(FRAME_CFG, "w", encoding="utf-8") as f:
            json.dump({"_说明": "取景框放大倍数。程序里按 [ / ] 会自动改这里，也可手改。",
                       "zoom": round(z, 2)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[warn] 保存 frame.json 失败:", e)

DEFAULT_LLM = {
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "",
    "model": "qwen-turbo",       # 便宜够快；也可换 qwen-plus / qwen-max / qwen-flash
    "timeout": 12,               # 单次请求超时（秒）
    "max_tokens": 320,           # 回复上限，面板放不下太长的话
}


def load_llm_config():
    cfg = dict(DEFAULT_LLM)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f).get("llm", {}) or {}
        for k in cfg:
            if user.get(k) not in (None, ""):
                cfg[k] = user[k]
    except FileNotFoundError:
        pass
    except Exception as e:
        print("[warn] config.json 读取失败，改用内置默认:", e)

    # 环境变量可临时覆盖，方便不改文件就换模型
    if os.environ.get("HAND_OCR_LLM_URL"):
        cfg["base_url"] = os.environ["HAND_OCR_LLM_URL"]
    key = os.environ.get("HAND_OCR_LLM_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if key:
        cfg["api_key"] = key
    if os.environ.get("HAND_OCR_LLM_MODEL"):
        cfg["model"] = os.environ["HAND_OCR_LLM_MODEL"]
    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")
    return cfg


LLM_CFG = load_llm_config()
LLM_URL = LLM_CFG["base_url"]
LLM_KEY = str(LLM_CFG["api_key"]).strip()
LLM_MODEL = str(LLM_CFG["model"])
LLM_TIMEOUT = float(LLM_CFG["timeout"])
LLM_MAXTOK = int(LLM_CFG["max_tokens"])
# 详细讲解（译文 + 200~350 字讲解 + 关键词）要的输出量远超 320，config 里写小了也按这个走
LLM_MAXTOK_EXPLAIN = max(LLM_MAXTOK, 900)
LLM_ON = bool(LLM_KEY) and not LLM_KEY.startswith("在这里")


def provider_name():
    """从 base_url 猜厂商名，猜不准就统一叫「大模型」。"""
    h = (LLM_URL or "").lower()
    for kw, name in (("dashscope", "千问"), ("aliyuncs", "千问"),
                     ("deepseek", "DeepSeek"), ("bigmodel", "智谱"),
                     ("moonshot", "Kimi"), ("siliconflow", "硅基流动"),
                     ("openai", "OpenAI")):
        if kw in h:
            return name
    return "大模型"


def llm_status():
    """给控制台用的一行状态说明。"""
    if LLM_ON:
        host = LLM_URL.split("//")[-1].split("/")[0]
        return "%s 已接入：%s @ %s" % (provider_name(), LLM_MODEL, host)
    if not LLM_KEY:
        return "未配置 API Key → 纯离线模式（只用 glossary.json）"
    return "API Key 还是占位符，请填真 Key → 暂时纯离线"


# ============================ 工具 ============================
def _dist(a, b):
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def hand_ratios(hand):
    """返回 (四指比值, 手掌尺寸)。比值 = |指尖-手腕| / |指根-手腕|，与手的远近无关。"""
    wrist = hand[0]
    rs = []
    for tip, pip in ((8, 6), (12, 10), (16, 14), (20, 18)):
        rs.append(_dist(hand[tip], wrist) / max(1e-6, _dist(hand[pip], wrist)))
    return rs, _dist(hand[0], hand[9])


def beep(freq=880, ms=70):
    if winsound:
        try:
            winsound.Beep(freq, ms)
        except Exception:
            pass


def norm_text(s):
    """OCR 文本归一化：去掉所有空白，统一全半角标点，便于匹配。"""
    if not s:
        return ""
    trans = str.maketrans("，。：；（）【】、！？“”‘’", ",.:;()[]、!?\"\"''")
    return "".join(s.translate(trans).split()).lower()


def looks_english(s):
    """判断框选内容是不是英文（需要翻译）。

    判据：至少有一个 2 个字母以上的英文单词，且字母数明显多于汉字数。
    故意不算单个大写字母 —— 否则 P(A|B)=P(B|A)P(A)/P(B) 这种纯公式会被当成英文。
    """
    if not s:
        return False
    words = re.findall(r"[A-Za-z]{2,}", s)
    letters = sum(1 for ch in s if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return bool(words) and letters > cjk * 2


def _clip_natural(s, limit):
    """把文本裁到 limit 字以内，尽量切在句末，避免半句话被硬切断。"""
    if not s or len(s) <= limit:
        return s
    head = s[:limit]
    cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"),
              head.rfind("；"), head.rfind("\n"))
    if cut >= limit * 0.55:
        return head[:cut + 1]
    return head.rstrip() + "…"


def _parse_json_loose(s):
    """从模型输出里抠出 JSON 对象：容忍 ```json 围栏、前后寒暄、被截断的尾巴。"""
    if not s:
        return None
    t = re.sub(r"```(?:json)?", "", s).strip()
    try:
        o = json.loads(t)
        if isinstance(o, dict):
            return o
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            o = json.loads(m.group(0))
            if isinstance(o, dict):
                return o
        except Exception:
            pass
    return None


def wrap_cjk(text, max_px, font, tkfont_mod):
    """按像素宽度手工折行。Tk 的 -width 只在空格处断行，中文段落会直接出界。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if tkfont_mod.Font.measure(font, cur + ch) > max_px and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return "\n".join(lines)


# ========================== 释义引擎 ==========================
class Explainer:
    """离线文本匹配释义。可选叠加 LLM 生成更自然的讲解。"""

    def __init__(self, path=GLOSSARY):
        self.entries = []
        self.last_llm_ms = 0          # 上一次千问调用耗时，用于状态栏展示
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.entries = json.load(f).get("entries", [])
        except Exception as e:
            print("[warn] 释义库读取失败:", e)

    def match(self, text):
        """返回 (最佳条目, 其他命中条目列表)。按关键词长度排序，长词优先。"""
        t = norm_text(text)
        hits = []
        for e in self.entries:
            best = 0
            for kw in (e.get("term", ""), e.get("en", "")):
                k = norm_text(kw)
                if k and k in t:
                    best = max(best, len(k))
            if best:
                hits.append((best, e))
        hits.sort(key=lambda x: -x[0])
        if not hits:
            return None, []
        return hits[0][1], [h[1] for h in hits[1:3]]

    def explain(self, raw_text):
        """返回 dict(title, en, trans, body, tag, extra, footer)。

        trans 是中文译文（原文是中文时为空），body 是中文详细讲解。
        """
        self.last_llm_ms = 0
        entry, others = self.match(raw_text)
        text_clean = " ".join(raw_text.split())
        footer = "识别文本：" + text_clean[:80]
        naive_en = looks_english(text_clean)
        llm = self._llm(text_clean, entry.get("term", "") if entry else "")

        # 大模型可用：译文 + 详细讲解（这是主力路径）
        if llm:
            lang = (llm.get("lang") or "").lower()
            if lang not in ("en", "zh", "mixed"):
                lang = "en" if naive_en else "zh"
            trans = (llm.get("translation") or "").strip()
            # 术语命中且译文就是这个术语本身，就别重复显示两遍
            if entry and norm_text(trans) == norm_text(entry.get("term", "")):
                trans = ""
            body = (llm.get("explain") or "").strip()
            terms = [t for t in (llm.get("terms") or []) if isinstance(t, dict) and t.get("w")]
            if terms:
                body += "\n\n关键词：" + "；".join(
                    "%s（%s）" % (str(t.get("w"))[:24], str(t.get("m", ""))[:40])
                    for t in terms[:3])
            if entry:
                title, en = entry.get("term", ""), entry.get("en", "")
            elif lang in ("en", "mixed") or naive_en:
                title, en = "英文翻译", ""
            else:
                title, en = "AI 解读", ""
            extra = ("另匹配到：" + "、".join(o.get("term", "") for o in others)) if others else ""
            return {"title": title, "en": en, "trans": trans, "body": body,
                    "tag": provider_name() + " · " + LLM_MODEL,
                    "extra": extra, "footer": footer}

        # 没有大模型 —— 只有术语库，做不了整句翻译，但要说清楚为什么
        hint = ""
        if naive_en:
            hint = ("\n\n框选的是英文，整句翻译和详细讲解需要大模型："
                    "把 API Key 填进 hand-ocr/config.json 的 llm.api_key"
                    "（或双击「配置大模型.bat」），再框一次就有中文译文了。")

        if entry is None:
            body = ("术语库里没有匹配条目，当前是纯离线模式。"
                    "在 hand-ocr/config.json 里填上 API Key（或双击 配置大模型.bat），"
                    "就能对任意框选内容做开放式解读。") + hint
            return {"title": "英文（未翻译）" if naive_en else "未收录内容",
                    "en": "", "trans": "", "body": body, "tag": "离线释义库",
                    "extra": "", "footer": footer}

        extra = ("另匹配到：" + "、".join(o.get("term", "") for o in others)) if others else ""
        return {"title": entry.get("term", ""), "en": entry.get("en", ""), "trans": "",
                "body": entry.get("body", "") + hint, "tag": entry.get("kind", "术语"),
                "extra": extra, "footer": footer}

    @staticmethod
    def _clean_md(s):
        """面板是纯文本渲染，Markdown 记号会原样显示成 ** ### 之类，必须洗掉。"""
        if not s:
            return ""
        s = re.sub(r"```.*?```", "", s, flags=re.S)     # 代码块
        s = re.sub(r"[*_`#>]+", "", s)                  # 粗体/斜体/标题/引用
        s = re.sub(r"^\s*[-–—•]\s+", "", s, flags=re.M)  # 列表符号
        s = re.sub(r"[ \t]{2,}", " ", s)
        s = re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()

    def _llm(self, text, term=""):
        """调用大模型（OpenAI 兼容接口）。

        返回 dict(lang, translation, explain, terms)；失败返回 None（调用方回退离线释义）。
        用 JSON 约束输出，这样"译文"和"讲解"能分开放进面板，不用猜哪句是译文。
        """
        if not LLM_ON:
            return None

        sys_msg = (
            "你是大学课堂助教。学生用摄像头框选了屏幕上的一段文字（教材、课件或论文），"
            "OCR 结果可能有个别错字、断行或公式错乱，请自行容错理解。\n"
            "严格只输出一个 JSON 对象，不要代码块围栏，不要任何解释性文字。键固定为：\n"
            '{"lang":"en|zh|mixed","translation":"中文译文","explain":"中文详细讲解",'
            '"terms":[{"w":"原文关键词","m":"中文释义"}]}\n'
            "要求：\n"
            "1. translation：把原文完整、通顺地译成中文。公式、符号、变量名保持原样不翻译；"
            "原文本来就是中文时给空字符串。\n"
            "2. explain：200~350 字中文讲解，按三层写：①这段话到底在讲什么（先一句话点题）"
            "②把关键概念逐个拆开讲清楚，出现公式就逐个说明符号含义、成立条件和直觉意义"
            "③最容易搞错的地方，或者它为什么重要。要具体，不要套话，可以打比方。\n"
            "3. terms：挑 1~3 个最关键的术语，给中文释义，每条不超过 20 字。\n"
            "4. 全部使用纯文本，不要 Markdown 记号，不要分点编号。"
        )
        if term:
            sys_msg += "\n这段内容的核心术语是「%s」，讲解请围绕它展开。" % term

        payload = json.dumps({
            "model": LLM_MODEL,
            "temperature": 0.3,
            "max_tokens": LLM_MAXTOK_EXPLAIN,
            "messages": [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": "框选内容：\n" + text[:800]},
            ],
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            LLM_URL + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + LLM_KEY})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            msg = data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:240]
            except Exception:
                pass
            print("[llm] HTTP %s %s" % (e.code, detail))
            return None
        except Exception as e:
            print("[llm] 调用失败:", type(e).__name__, e)
            return None

        self.last_llm_ms = int((time.time() - t0) * 1000)
        obj = _parse_json_loose(msg)
        if obj is None:
            # 模型没按格式来：整段当讲解，宁可少个译文也别让整张卡片空掉
            out = self._clean_md(msg)
            print("[llm] %d ms（JSON 解析失败，按纯文本处理）" % self.last_llm_ms)
            return {"lang": "", "translation": "", "explain": out[:900], "terms": []}

        res = {"lang": str(obj.get("lang") or ""),
               "translation": self._clean_md(str(obj.get("translation") or ""))[:400],
               "explain": self._clean_md(str(obj.get("explain") or ""))[:900],
               "terms": obj.get("terms") if isinstance(obj.get("terms"), list) else []}
        print("[llm] %d ms  lang=%s 译%d字 讲%d字" % (
            self.last_llm_ms, res["lang"] or "?", len(res["translation"]), len(res["explain"])))
        return res


# ========================== 屏幕浮层 ==========================
class Overlay:
    """全屏透明置顶浮层：画取景框 + 进度条 + 结果面板。鼠标可穿透。"""

    def __init__(self):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk = tk
        self.tkfont = tkfont

        self.root = tk.Tk()
        # 屏幕尺寸直接问 Tk 自己，保证画布坐标、窗口尺寸、截图坐标三者同一套像素
        self.sw = self.root.winfo_screenwidth()
        self.sh = self.root.winfo_screenheight()
        sw, sh = self.sw, self.sh
        self.root.overrideredirect(True)
        self.root.geometry("%dx%d+0+0" % (sw, sh))
        self.root.configure(bg=TRANSPARENT_KEY)
        try:
            self.root.attributes("-topmost", True)
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception as e:
            print("[warn] 透明色设置失败（浮层会变成不透明黑底）:", e)

        self.canvas = tk.Canvas(self.root, width=sw, height=sh, bg=TRANSPARENT_KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.root.update()
        self._click_through()

        fam = self._pick_font()
        self.fam = fam
        self._tfonts = {}
        self.f_big = tkfont.Font(family=fam, size=21, weight="bold")
        self.f_body = tkfont.Font(family=fam, size=13)
        self.f_small = tkfont.Font(family=fam, size=10)
        self.f_hint = tkfont.Font(family=fam, size=11)

        # 取景框：四角括号（8 条线）+ 四条细边
        self.corner_ids = [self.canvas.create_line(0, 0, 1, 1, fill=C_FRAME, width=4,
                                                   capstyle="round") for _ in range(8)]
        self.edge_ids = [self.canvas.create_line(0, 0, 1, 1, fill=C_FRAME_DIM, width=1)
                         for _ in range(4)]
        self.prog_bg = self.canvas.create_line(0, 0, 1, 1, fill="#123038", width=7,
                                               capstyle="round")
        self.prog_fg = self.canvas.create_line(0, 0, 1, 1, fill=C_OK, width=7,
                                               capstyle="round")
        self.prog_txt = self.canvas.create_text(0, 0, text="", fill=C_OK,
                                                font=self.f_hint, anchor="sw")
        self.hint = self.canvas.create_text(sw // 2, sh - 100, text="", fill="#8FE9FF",
                                            font=self.f_hint, justify="center")
        self._panel_key = None

    # ---- 初始化 ----
    def _pick_font(self):
        avail = set(self.tkfont.families())
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "SimHei", "SimSun"):
            if name in avail:
                return name
        return "TkDefaultFont"

    def _click_through(self):
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            st = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                st | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception as e:
            print("[warn] 鼠标穿透设置失败:", e)

    def pump(self):
        try:
            self.root.update()
        except Exception:
            pass

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---- 画面 ----
    def set_hint(self, text, color="#8FE9FF"):
        self.canvas.itemconfigure(self.hint, text=text, fill=color)

    def clear_frame(self):
        for i in self.corner_ids + self.edge_ids + [self.prog_bg, self.prog_fg]:
            self.canvas.coords(i, -20, -20, -20, -20)
        self.canvas.coords(self.prog_txt, -20, -20)   # 文字项只接受 x,y

    def draw_frame(self, box, progress):
        """box = (x0,y0,x1,y1) 屏幕像素；progress 0~1。"""
        x0, y0, x1, y1 = box
        arm = max(18, min(60, int(min(x1 - x0, y1 - y0) * 0.18)))
        seg = [  # 四角括号，每角两条线
            (x0, y0, x0 + arm, y0), (x0, y0, x0, y0 + arm),
            (x1, y0, x1 - arm, y0), (x1, y0, x1, y0 + arm),
            (x0, y1, x0 + arm, y1), (x0, y1, x0, y1 - arm),
            (x1, y1, x1 - arm, y1), (x1, y1, x1, y1 - arm),
        ]
        for item, c in zip(self.corner_ids, seg):
            self.canvas.coords(item, *c)
        edges = [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]
        for item, c in zip(self.edge_ids, edges):
            self.canvas.coords(item, *c)

        py = max(6, y0 - 16)
        self.canvas.coords(self.prog_bg, x0, py, x1, py)
        self.canvas.coords(self.prog_fg, x0, py, x0 + (x1 - x0) * max(0.0, min(1.0, progress)), py)
        self.canvas.coords(self.prog_txt, x0, py - 8)
        self.canvas.itemconfigure(
            self.prog_txt,
            text="稳住取景 %.1fs / %.1fs" % (progress * HOLD_SEC, HOLD_SEC),
            fill=C_OK if progress < 1 else C_FRAME)

    # ---- 结果面板 ----
    def _ls(self, font):
        return self.tkfont.Font.metrics(font, "linespace")

    def _title_font(self, text, max_px):
        """标题自动缩号：从 20pt 往下试，直到能塞进可用宽度。"""
        for size in (20, 18, 17, 16, 15, 14, 13, 12):
            f = self._tfonts.get(size)
            if f is None:
                f = self.tkfont.Font(family=self.fam, size=size, weight="bold")
                self._tfonts[size] = f
            if self.tkfont.Font.measure(f, text) <= max_px:
                return f
        return self._tfonts[12]

    def show_panel(self, box, info):
        tag = info.get("tag", "")
        en = info.get("en", "") or ""
        trans = (info.get("trans", "") or "").strip()
        key = (info["title"], en, trans, info["body"], info.get("footer", ""), tag, box)
        if key == self._panel_key:
            return
        self._panel_key = key
        self.canvas.delete("panel")

        pad = 18
        pad_l = pad + 10                       # 文字左边距，让开左侧强调条
        cw = max(6, self.tkfont.Font.measure(self.f_body, "中"))
        pw = int(21 * cw + 2 * pad + 10)       # 约 21 个汉字一行
        pw = max(400, min(pw, int(self.sw * 0.42), self.sw - 80))
        inner = pw - pad * 2 - 10

        chip_w = (self.tkfont.Font.measure(self.f_small, tag) + 18) if tag else 0
        tfont = self._title_font(info["title"], max(120, inner - chip_w - 12))
        h_title = self._ls(tfont)
        h_en = self._ls(self.f_small) if en else 0
        ls_body = self._ls(self.f_body)
        h_small = self._ls(self.f_small)
        extra = info.get("extra", "") or ""
        foot = wrap_cjk(info.get("footer", "") or "", inner, self.f_small, self.tkfont)
        h_extra = (h_small + 6) if extra else 0
        h_foot = (foot.count("\n") * (h_small + 2) + h_small + 6) if foot else 0

        # 译文块：小标签 + 一块青绿色正文，跟讲解在视觉上分开
        trans_txt = wrap_cjk(trans, inner, self.f_body, self.tkfont) if trans else ""
        h_trans = 0
        if trans:
            h_trans = (h_small + 2) + (trans_txt.count("\n") * (ls_body + 2) + ls_body) + 10
            if info["body"]:
                h_trans += h_small + 2        # 下面那行「AI 讲解」小标签也要占位

        overhead = pad + h_title + h_en + 8 + h_trans + h_extra + h_foot + pad \
            + (16 if foot else 0)     # 给右下角"握拳关闭"留位

        # 讲解太长时按行数反算能留多少字，保证面板永远塞得进屏幕（模型偶尔会超字数）
        body_plain = info["body"]
        body_txt = wrap_cjk(body_plain, inner, self.f_body, self.tkfont)
        h_body = body_txt.count("\n") * (ls_body + 2) + ls_body
        for _ in range(6):
            ph = overhead + h_body
            if ph <= self.sh - 24 or len(body_plain) < 80:
                break
            allowed = max(3, (self.sh - 24 - overhead) // max(1, ls_body + 2))
            lines_now = body_txt.count("\n") + 1
            per_line = max(4, len(body_plain) // max(1, lines_now))
            body_plain = _clip_natural(info["body"], max(80, int(allowed * per_line * 0.94)))
            body_txt = wrap_cjk(body_plain, inner, self.f_body, self.tkfont)
            h_body = body_txt.count("\n") * (ls_body + 2) + ls_body
        ph = overhead + h_body

        # 位置：优先放取景框右侧，放不下放左侧，再不行贴左边
        x0, y0, x1, y1 = box
        px = x1 + 26
        if px + pw > self.sw - 12:
            px = x0 - 26 - pw
        if px < 12:
            px = max(12, min(self.sw - pw - 12, x0))
        py = y0
        if py + ph > self.sh - 12:
            py = self.sh - 12 - ph
        if py < 12:
            py = 12

        # 先铺底板，再写字，这样文字天然在最上层，不用来回 raise
        self._round_rect(px, py, px + pw, py + ph, 14,
                         fill=C_PANEL_BG, outline=C_FRAME, width=2)
        self.canvas.create_rectangle(px + 2, py + pad, px + 6, py + ph - pad,
                                     fill=C_FRAME, outline="", tags="panel")
        if tag:
            tx1, ty0 = px + pw - pad, py + pad - 4
            self._round_rect(tx1 - chip_w, ty0, tx1, ty0 + h_small + 8, 9,
                             fill="#123038", outline=C_OK, width=1)
            self.canvas.create_text(tx1 - chip_w / 2.0, ty0 + (h_small + 8) / 2.0,
                                    text=tag, fill=C_OK, font=self.f_small, tags="panel")

        cy = py + pad
        self.canvas.create_text(px + pad_l, cy, text=info["title"], anchor="nw",
                                fill=C_TITLE, font=tfont, tags="panel")
        cy += h_title
        if en:
            self.canvas.create_text(px + pad_l, cy + 1, text=en, anchor="nw",
                                    fill=C_FOOT, font=self.f_small, tags="panel")
            cy += h_en + 4
        cy += 8
        if trans:
            self.canvas.create_text(px + pad_l, cy, text="中文译文", anchor="nw",
                                    fill=C_FOOT, font=self.f_small, tags="panel")
            cy += h_small + 2
            self.canvas.create_text(px + pad_l, cy, text=trans_txt, anchor="nw",
                                    fill=C_OK, font=self.f_body, justify="left",
                                    tags="panel")
            cy += trans_txt.count("\n") * (ls_body + 2) + ls_body + 10
            if body_txt:
                self.canvas.create_text(px + pad_l, cy, text="AI 讲解", anchor="nw",
                                        fill=C_FOOT, font=self.f_small, tags="panel")
                cy += h_small + 2
        self.canvas.create_text(px + pad_l, cy, text=body_txt, anchor="nw",
                                fill=C_BODY, font=self.f_body, justify="left",
                                tags="panel")
        cy += h_body + 6
        if extra:
            self.canvas.create_text(px + pad_l, cy, text=extra, anchor="nw",
                                    fill=C_FOOT, font=self.f_small, tags="panel")
            cy += h_extra
        if foot:
            self.canvas.create_text(px + pad_l, cy, text=foot, anchor="nw",
                                    fill=C_FOOT, font=self.f_small, justify="left",
                                    tags="panel")
        self.canvas.create_text(px + pw - pad, py + ph - 10,
                                text="握拳关闭", anchor="se",
                                fill=C_FRAME_DIM, font=self.f_small, tags="panel")

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0,
               x1 - r, y0, x1, y0, x1, y0 + r,
               x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r,
               x0, y0 + r, x0, y0]
        return self.canvas.create_polygon(pts, smooth=True, tags="panel", **kw)

    def hide_panel(self):
        self._panel_key = None
        self.canvas.delete("panel")


# ========================== 取景稳定判定 ==========================
class FrameTracker:
    """判断"双手框住一块区域并稳住"。"""

    def __init__(self):
        self.box = None
        self.t0 = 0.0
        self.fired = False
        self.progress = 0.0

    def reset(self):
        self.box = None
        self.t0 = 0.0
        self.fired = False
        self.progress = 0.0

    def update(self, box, now):
        """box 为归一化 (x0,y0,x1,y1) 或 None。返回 'fire' / 'holding' / 'idle'。"""
        if box is None:
            self.reset()
            return "idle"

        if self.box is None:
            self.box = list(box)
            self.t0 = now
            self.fired = False
        else:
            stable = all(abs(a - b) <= HOLD_TOL for a, b in zip(box, self.box))
            self.box = [self.box[i] * (1 - SMOOTH) + box[i] * SMOOTH for i in range(4)]
            if not stable:          # 手在动：重新计时，并允许再次触发
                self.t0 = now
                self.fired = False

        self.progress = min(1.0, (now - self.t0) / HOLD_SEC)
        if self.progress >= 1.0 and not self.fired:
            self.fired = True
            return "fire"
        return "holding"


class SlideTracker:
    """单手横向滑动 → 翻页。

    只认"张开的手"（伸出 >= SWIPE_MIN_FINGERS 指）：做取景手势时手是 1~2 指，
    不会被当成滑动。双手一出现就上锁 SLIDE_LOCK 秒，避免取景刚收手时顺手挥一下就翻页。

    挥手之后手一定会"回位"，而回位和"故意往反方向滑"在几何上完全是同一个动作
    （实测：左挥后 0.1s 手就往回走，回位位移 0.13 > 阈值 0.10）。只能靠时序区分：

        1) 回撤复位——同方向要再翻一页，手必须先往回撤 RESET_DIST。
           否则一次幅度大的长挥会在滑动途中连发好几次。
        2) 反向锁定——翻页后反向滑动一律吞掉（它几乎必然是手在回位），
           直到手真正停够 REVERSE_RELEASE 秒才解禁。

    效果：连挥两次左滑 = 挥、回位（吞掉）、挥 → 正好两次翻页，中间不用刻意停顿；
          真想翻回上一页，手停半秒再往反方向挥即可。
    """

    def __init__(self):
        self.buf = collections.deque()      # 本次动作的轨迹
        self.hist = collections.deque()     # 近 SPEED_WIN 秒的轨迹，判停稳用
        self.armed = False                  # 手刚入镜/取景刚结束时，先停稳再允许判定
        self.speed = 0.0                    # 速度估计，只给预览窗显示
        self.block_dir = ""                 # 当前被锁住的方向（回位会被吞掉）
        self.block_until = 0.0
        self.retract_ok = True              # 是否已回撤复位（同向连发的闸门）
        self.retract_peak = None            # 上次动作后，掌心沿动作方向到过的最远位置
        self.ext_sign = 1                   # 上次动作的横向符号：+1 向画面右 / -1 向左
        self.last_action = -99.0
        self.lock_until = 0.0
        self.dir = ""                       # 最近一次判定方向，给预览窗显示
        self.why = "no-hand"                # 本帧为什么没触发，直接显示在预览窗上排查用
        self.lost_until = 0.0               # 手丢失宽限到什么时候
        self.gone_since = None              # 本次离开画面的起始时刻（None = 手还在）
        self.post_action_guard = 0.0        # 动作后的反向吞咽窗口

    def reset(self, now=None):
        """本帧没检测到手：清轨迹并记一个宽限期。

        宽限期内重新入画也不判定——手缩回去时常常先出画再回来，
        回来时半个身子已经在这段轨迹里，直接累计就会把"回程"当成一次新挥动。
        """
        self.buf.clear()
        self.hist.clear()
        if now is not None and self.gone_since is None:
            self.gone_since = now           # 记下"开始离开"的时刻
            self.armed = False

    def lock(self, now):
        """双手出现：清轨迹并短暂上锁，同时要求收手后重新停稳。"""
        self.buf.clear()
        self.hist.clear()
        self.armed = False
        self.block_dir = ""
        self.lock_until = now + SLIDE_LOCK

    def _dwell(self, now, cx, cy, span, radius=None):
        """掌心在最近 span 秒内是否始终待在半径 STILL_RADIUS 的小圈里。

        容差必须小于一帧（0.02s）：否则窗口最老的那个样本会被边界条件跳过的，
        实际停留时间会比设定值短，手在折返点的慢速段就会被误判成"停够"。
        """
        if not self.hist:
            return False
        if now - self.hist[0][0] < span - 0.02:   # 样本时间跨度还不够，先别下结论
            return False
        for t, x, y in self.hist:
            if now - t <= span + 1e-6 and \
                    (x - cx) ** 2 + (y - cy) ** 2 > (radius or STILL_RADIUS) ** 2:
                return False
        return True

    def update(self, hand, now, fast=False):
        """hand 为单只手的 21 个关键点（或 None）。返回 'next' / 'prev' / ''。

        fast=True 表示"食指+中指连翻模式"：冷却更短，允许连续同向快速翻页。
        回撤复位（RESET_DIST）在两种模式下都要满足——这是"一次长挥不连发好几页"
        的唯一保障，放松了就会把一个动作识别成三个。
        """
        if hand is None:
            self.reset(now)               # 记一个宽限期，缩手出画再入画不算新动作
            self.why = "no-hand"
            return ""
        if now < self.lock_until:
            self.buf.clear()
            self.hist.clear()
            self.why = "lock(2hands)"
            return ""

        rs, palm = hand_ratios(hand)
        if palm < MIN_PALM:
            self.buf.clear()          # 噪点帧
            self.hist.clear()
            self.why = "noise"
            return ""
        # 手刚回来：只有"真的离开过"（> LOST_MIN_GAP）才设宽限，
        # 检测闪断一两帧不设——否则正常挥到一半掉一帧，后面的动作全被吞掉。
        if self.gone_since is not None:
            gone = now - self.gone_since
            self.gone_since = None
            if gone >= LOST_MIN_GAP and LOST_GRACE > 0:
                self.lost_until = now + LOST_GRACE
        if now < self.lost_until:     # 刚丢失过：重新入画也要重新攒轨迹
            self.buf.clear()
            self.hist.clear()
            self.armed = False
            self.why = "lost-grace"
            return ""
        if palm < MIN_PALM_SWIPE:
            self.buf.clear()
            self.hist.clear()
            self.why = "palm-small"
            return ""
        # 阈值按手掌尺寸归一化（普适性）
        swipe_thr = min(PALM_MAX_THR, max(PALM_MIN_THR, (SWIPE_DIST_PALM_FAST if fast else SWIPE_DIST_PALM) * palm))
        reset_thr = min(PALM_MAX_THR * 1.2, max(PALM_MIN_THR, RESET_DIST_PALM * palm))
        if fast:
            # 连翻模式放宽回撤要求：0.90 个手掌宽对快速连滑太苛刻，手退不到那么远，
            # 第二次同向滑动就被当成没回撤吞掉（用户实测）。0.25 仍要求方向反转，
            # 所以一次长滑（不回头）还是只算一次。
            reset_thr = max(0.03, 0.25 * palm)
        # 停稳半径：按手掌缩放，但必须封顶——放太大时"手在回程中抖一下"都会被当成停稳，
        # 反而让反向锁定失效（回归用例 ⑥ 复现过）。
        still_r = max(0.022, min(0.045, STILL_RADIUS_PALM * palm))
        if fast:
            # 连翻模式：食指+中指伸出即可（其余手指收拢），不要求张满五指
            pose_ok = rs[0] > TWO_FINGER_INDEX and rs[1] > TWO_FINGER_INDEX
            why_bad = "fast-pose"
        else:
            # 单次模式：必须是张开的手（>=3 指），避免做取景手势时误翻
            pose_ok = sum(1 for r in rs if r > INDEX_RATIO) >= SWIPE_MIN_FINGERS
            why_bad = "fingers<"
        if not pose_ok:
            self.armed = False        # 关键：手型一旦不对就重新攒帧，
            self.buf.clear()          # 否则"收手 → 再张开"会从半截轨迹继续算
            self.hist.clear()
            self.why = why_bad
            return ""

        cx = (hand[0].x + hand[9].x) / 2.0
        cy = (hand[0].y + hand[9].y) / 2.0

        # ---------- 1) 缓存最近 SPEED_WIN 秒的轨迹 ----------
        self.hist.append((now, cx, cy))
        while len(self.hist) > 4 and now - self.hist[0][0] > SPEED_WIN:
            self.hist.popleft()
        # 速度只用于预览窗显示，取最近 0.15 秒的位移
        old = None
        for t, x, y in self.hist:
            if now - t <= 0.15:
                old = (t, x, y)
                break
        if old and now - old[0] > 0.05:
            self.speed = (((cx - old[1]) ** 2 + (cy - old[2]) ** 2) ** 0.5) / (now - old[0])
        else:
            self.speed = 9.9          # 刚恢复检测、样本不够，当作"在动"

        # ---------- 2) 累计轨迹（先入队，后面要数帧数）----------
        self.buf.append((now, cx, cy))
        while self.buf and now - self.buf[0][0] > (SWIPE_WINDOW_FAST if fast else SWIPE_WINDOW):
            self.buf.popleft()

        # ---------- 3) 解锁 ----------
        # 轨迹一建立（>=4 帧 ≈ 0.13s）就允许判定：老版本死等"停稳 0.16 秒"，
        # 手一入镜就挥的那一下会被整段吞掉，是"挥手没反应"的主要来源之一。
        if len(self.buf) >= 4:
            self.armed = True
        # 注意：守卫窗口内不许解除反向锁定。挥手后的"回位"常常是一个 V 形轨迹，
        # 中点附近会在 0.2 秒里一直待在小圈内 → 被误判成"停稳" → 锁定被解开 →
        # 回程立刻被当成一次反向翻页。守卫期内直接不给解。
        if now >= self.post_action_guard and self._dwell(now, cx, cy, REVERSE_RELEASE, still_r):
            self.block_dir = ""           # 手停够了，允许往回翻
        if self.block_dir and now > self.block_until:
            self.block_dir = ""           # 保险丝：锁定不会无限期

        # ---------- 4) 回撤复位跟踪：手是否已从上次动作的终点往回撤过 ----------
        if self.retract_peak is not None and not self.retract_ok:
            if (cx - self.retract_peak) * self.ext_sign > 0:
                self.retract_peak = cx                      # 还在往同方向推进，更新最远点
            elif (self.retract_peak - cx) * self.ext_sign >= RESET_DIST:
                self.retract_ok = True                      # 回撤到位，可以再同向翻一页

        if not self.armed or len(self.buf) < 4:
            self.why = "arming"
            return ""
        cd = SWIPE_COOLDOWN_FAST if fast else SWIPE_COOLDOWN
        if (now - self.last_action) < cd:
            self.why = "cool"
            return ""

        xs = [b[1] for b in self.buf]
        ys = [b[2] for b in self.buf]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        # 必须是一次明显的横向挥动；纯竖直的抬手不算
        if span_x < swipe_thr:
            self.why = "short%.2f" % span_x
            return ""
        if span_y > span_x * SWIPE_MAX_TILT:
            self.why = "tilt%.1f" % (span_y / max(1e-6, span_x))
            return ""

        # 方向不看"首尾差"，看两个极值谁先出现：
        # 挥手后手会往回走，首尾差可能被回程抵消；而"先到哪一头、后到哪一头"
        # 在一整段动作里是稳定的。
        go_right = xs.index(max(xs)) > xs.index(min(xs))
        dx_net = xs[-1] - xs[0]
        # 净位移必须与方向一致且足够大，否则是"划过去又原路划回"的抖动
        if abs(dx_net) < max(swipe_thr * 0.5, span_x * 0.5) or (dx_net > 0) != go_right:
            self.why = "round-trip"
            return ""
        # 缩手回位：手在收回去时速度很快，但净位移远小于跨度——
        # 真实的挥动是"一路推过去"，回位是"到顶后折返"，用这两条区分。
        if self.speed > ENDPOINT_SPEED and abs(dx_net) < span_x * 0.65:
            self.why = "returning"
            return ""

        # 关键：主循环已对画面做过 cv2.flip 镜像，镜像后"手往用户左侧挥"表现为 x 减小。
        # 2026-10-22 用手机正拍录屏复核过：未镜像时左挥 = cx 递增，镜像后 = cx 递减。
        # 所以 go_right=True 才是"向右划"。曾把这里写成 dx>0 → next，方向反了半个屏幕，
        # 表现为"左滑在第一页上无任何反应"。
        act = ("prev" if go_right else "next") if SWIPE_LEFT_IS_NEXT \
            else ("next" if go_right else "prev")

        # 动作后的短窗口内，反向动作一律吞掉（回位的高发期）
        if now < self.post_action_guard and act == self.block_dir:
            self.buf.clear()
            self.why = "post-guard"
            return ""

        # ---------- 5) 反向锁定：吞掉手回位那一下 ----------
        if self.block_dir and act == self.block_dir:
            # 注意：这里不能无条件 retract_ok=True。挥手尾部"抖一下再往前推"也会被
            # 判成反向，若无条件放行，同一个动作会在 0.4 秒后再翻一页（实测录屏就有）。
            # 只有真的退回到 retract_peak 之内 RESET_DIST，才算复位完成。
            if self.retract_peak is not None and \
                    (self.retract_peak - cx) * self.ext_sign >= reset_thr:
                self.retract_ok = True
            self.buf.clear()              # 当作没发生，清轨迹，手继续回位也不会连发
            self.why = "return-swallowed"
            return ""

        # ---------- 6) 同方向但还没回撤：一次长挥不连发 ----------
        if act == self.dir and not self.retract_ok:
            self.buf.clear()
            self.why = "no-retract"
            return ""

        self.last_action = now
        self.buf.clear()
        self.dir = act
        self.block_dir = "prev" if act == "next" else "next"
        self.block_until = now + RETURN_TTL
        self.ext_sign = 1 if go_right else -1
        self.retract_peak = cx
        self.retract_ok = False
        self.post_action_guard = now + POST_ACTION_GUARD
        self.why = "FIRE"
        return self.dir


class RestoreTracker:
    """「复原」手势判定：五指张开且掌心静止 RESTORE_DWELL 秒。

    为什么不用"张开就恢复"：翻页手势本身就是张开手横移，如果张开即恢复，
    关掉面板后抬一下手就会把面板叫回来，等于关不掉。所以要求**静止**。
    """

    def __init__(self):
        self.hist = collections.deque()

    def reset(self):
        self.hist.clear()

    def update(self, hand, now):
        rs, _palm = hand_ratios(hand)
        cx = (hand[0].x + hand[9].x) / 2.0
        cy = (hand[0].y + hand[9].y) / 2.0
        if not all(r > OPEN_RATIO for r in rs):      # 只要有一指没伸开就不算张开手掌
            self.hist.clear()
            return False
        self.hist.append((now, cx, cy))
        while len(self.hist) > 6 and now - self.hist[0][0] > RESTORE_DWELL + 0.4:
            self.hist.popleft()
        if len(self.hist) < 4:
            return False
        if now - self.hist[0][0] < RESTORE_DWELL - 0.05:
            return False
        for t, x, y in self.hist:
            if now - t <= RESTORE_DWELL + 1e-6 and \
                    (x - cx) ** 2 + (y - cy) ** 2 > STILL_RADIUS ** 2:
                return False
        return True



def press_key(key):
    """给前台窗口发一个按键。没装 pyautogui 或发送失败就安静跳过。"""
    if pyautogui is None:
        return False
    try:
        pyautogui.press(key)
        return True
    except Exception as e:
        print("[warn] 按键失败:", e)
        return False


def press_hotkey(a, b):
    if pyautogui is None:
        return False
    try:
        pyautogui.hotkey(a, b)
        return True
    except Exception as e:
        print("[warn] 组合键失败:", e)
        return False


# ========================== 主程序 ==========================
def detect_frame_box(hands):
    """两只手都比"取景"姿势时，用两手拇指尖+食指尖围出的矩形作为取景框。

    姿势要求：食指伸出 + **中指收拢**（其余手指随意）。
    加中指这一条是为了和"张开手掌"区分开——左手张开是放大页面，
    如果只按"食指伸出"判，双手一起张开就会被误判成取景框。
    """
    if not hands or len(hands) < 2:
        return None
    ok = []
    for h in hands:
        rs, palm = hand_ratios(h)
        if palm < MIN_PALM:
            continue
        if rs[0] > INDEX_RATIO and rs[1] < FRAME_FOLD_MIDDLE:   # 食指伸、中指收
            ok.append(h)
    if len(ok) < 2:
        return None

    h_a, h_b = sorted(ok[:2], key=lambda h: (h[0].x + h[9].x) / 2.0)
    pts = [h_a[4], h_a[8], h_b[4], h_b[8]]      # 4=拇指尖 8=食指尖
    x0 = min(p.x for p in pts)
    x1 = max(p.x for p in pts)
    y0 = min(p.y for p in pts)
    y1 = max(p.y for p in pts)
    if (x1 - x0) < MIN_BOX_W or (y1 - y0) < MIN_BOX_H:
        return None
    return (max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1))


def any_fist(hands):
    """任意一只手呈握拳（四指比值全部 < FIST_MAX_RATIO）。"""
    for h in hands or []:
        rs, palm = hand_ratios(h)
        if palm >= MIN_PALM and max(rs) < FIST_MAX_RATIO:
            return True
    return False


def is_fist(hand):
    """单只手是否握拳。"""
    if hand is None:
        return False
    rs, palm = hand_ratios(hand)
    return palm >= MIN_PALM and max(rs) < FIST_MAX_RATIO


def is_open_palm(hand):
    """单只手是否五指张开（用于复原 / 左手放大）。"""
    if hand is None:
        return False
    rs, palm = hand_ratios(hand)
    if palm < MIN_PALM:
        return False
    return sum(1 for r in rs if r > OPEN_RATIO) >= MIN_OPEN_FINGERS


def is_two_finger(hand):
    """食指 + 中指伸出、无名指 + 小指收拢 → "连翻"手势。"""
    if hand is None:
        return False
    rs, palm = hand_ratios(hand)
    if palm < MIN_PALM:
        return False
    return (rs[0] > TWO_FINGER_INDEX and rs[1] > TWO_FINGER_INDEX
            and (rs[2] + rs[3]) / 2.0 < TWO_FINGER_FOLD)


SIDE_INVERT = os.environ.get("HAND_SIDE_INVERT", "1").strip().lower() not in (
    "0", "false", "no", "off")
SIDE_BY_GEOMETRY = os.environ.get("HAND_SIDE_GEOMETRY", "1").strip().lower() not in (
    "0", "false", "no", "off")


def side_by_geometry(hand):
    # 用关键点几何判左右手，不依赖模型标签。
    # 镜像自拍视角、掌心朝镜头且手指朝上时：右手拇指尖在小指根右侧，左手相反。
    # 比模型标签稳——实测过两次标签判反，导致左手动作落到右手的关闭分支里。
    if hand is None or len(hand) < 21:
        return ""
    dx = hand[4].x - hand[17].x      # 拇指尖 x - 小指根 x
    if abs(dx) < 0.02:
        return ""                     # 拇指与小指几乎重叠，判断不了朝向，放弃
    g = "R" if dx > 0 else "L"
    if SIDE_INVERT:                   # 摄像头驱动是否已镜像因机器而异，
        g = "L" if g == "R" else "R"  # 实测本机几何结果与真实左右相反
    return g


def norm_side(name, hand=None):
    # 把 MediaPipe 的 Left/Right 规整成 L/R；能按几何判就以几何为准。
    n = (name or "").strip().lower()
    if n.startswith("l"):
        s = "L"
    elif n.startswith("r"):
        s = "R"
    else:
        s = ""
    if SIDE_BY_GEOMETRY:
        g = side_by_geometry(hand)
        if g:
            return g                  # 几何优先，不再过 SIDE_SWAP
    if not s:
        return ""
    if SIDE_SWAP:
        s = "R" if s == "L" else "L"
    return s


def _zoom_norm(v):
    """摄像头坐标绕中心放大后夹回 [0,1]：放大后手不必张满画面，框也能贴到屏幕边缘。"""
    return min(1.0, max(0.0, 0.5 + (v - 0.5) * FRAME_ZOOM))


def box_to_screen(box, sw, sh):
    x0, y0, x1, y1 = box
    fx = lambda v: (v - SRC_L) / max(1e-6, SRC_R - SRC_L)
    fy = lambda v: (v - SRC_T) / max(1e-6, SRC_B - SRC_T)
    X0, X1 = fx(_zoom_norm(x0)), fx(_zoom_norm(x1))
    Y0, Y1 = fy(_zoom_norm(y0)), fy(_zoom_norm(y1))
    return (int(round(X0 * sw)), int(round(Y0 * sh)),
            int(round(X1 * sw)), int(round(Y1 * sh)))


def main():
    global FRAME_ZOOM
    # 让进程按物理像素工作，避免 125% 缩放时截图区域和浮层坐标对不上
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    FRAME_ZOOM = load_frame_zoom()

    for p, what in ((MODEL, "手部模型"), (GLOSSARY, "释义库")):
        if not os.path.exists(p):
            sys.exit("找不到%s：%s" % (what, p))

    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        sys.exit("缺少 OCR 依赖，请执行：pip install rapidocr_onnxruntime")

    print("正在加载模型与 OCR …")
    options = HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=MODEL),
        running_mode=RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    )
    landmarker = HandLandmarker.create_from_options(options)
    ocr = RapidOCR()
    explainer = Explainer()

    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        sys.exit("打不开摄像头：检查 CAM_INDEX，或关掉占用摄像头的程序（微信/Zoom 等）")

    # 摄像头刚 open 时头几帧经常拿不到画面（上一个占用者刚释放尤其如此），先等它热起来
    ready = False
    for _ in range(40):                       # 最多等 8 秒
        ok, _f = cap.read()
        if ok:
            ready = True
            break
        time.sleep(0.2)
    if not ready:
        cap.release()
        sys.exit(
            "摄像头已打开，但一直读不到画面。\n"
            "  最常见原因：摄像头被别的程序占着（微信/Zoom/相机/Camera 应用，"
            "或者上一次没关掉的本程序窗口）。\n"
            "  处理：把这些程序全关掉再重试。如果设备管理器里有多个摄像头，"
            "把文件顶部 CAM_INDEX 从 0 改成 1 试试。")

    overlay = Overlay()
    SW, SH = overlay.sw, overlay.sh
    print("屏幕尺寸 %dx%d" % (SW, SH))
    tracker = FrameTracker()
    slide = SlideTracker()
    restore = RestoreTracker()     # 右手张开静止 → 叫回刚关掉的面板
    lzoom = RestoreTracker()       # 左手张开静止 → 放大（复用同一套"张开+静止"判定）
    lstate = {"fist": 0, "last": -99.0, "open_latch": False}
    jobs = queue.Queue()          # OCR 线程 → 主线程 的结果通道
    state = {"busy": False, "panel": None, "fist": 0, "last_fire": -99.0,
             "fist_used": False,          # 一次握拳只算一次，必须重新张开才解锁
             "grab_count": 0, "last_grab": -99.0,
             "panel_box": None,           # 当前面板对应的屏幕框（复原时要原样画回去）
             "closed_panel": None,        # 刚被握拳关掉的面板 {"box":…, "info":…}
             "closed_at": -99.0,
             "fist_lock": 0.0,            # 关面板后短暂锁住 Esc/Win+D
             "status": "", "status_t": 0.0}

    def recognize(pil_img, box_px):
        """在工作线程里跑 OCR + 释义，结果塞回队列。"""
        try:
            arr = np.array(pil_img)[:, :, ::-1]        # RGB -> BGR
            if arr.shape[1] > 1400:                    # 大区域先缩放，保证耗时有上限
                s = 1400.0 / arr.shape[1]
                arr = cv2.resize(arr, (1400, max(1, int(arr.shape[0] * s))))
            t = time.time()
            # use_cls=False 关掉文字方向分类器：屏幕截图不存在倒置文字，
            # 实测快 30%~40%、结果完全一致
            res, _ = ocr(arr, use_cls=False)
            texts = [r[1] for r in res] if res else []
            joined = "  ".join(texts) if texts else ""
            info = explainer.explain(joined) if joined else {
                "title": "框内没有识别到文字", "tag": "OCR",
                "body": "把取景框对准有文字的幻灯片区域再试一次。纯图形、公式图片"
                        "如果太小，可以靠近一点让框更聚焦。",
                "extra": "", "footer": ""}
            info["_ms"] = int((time.time() - t) * 1000)
            info["_llm_ms"] = explainer.last_llm_ms
            info["_n"] = len(texts)
            info["_raw"] = joined
            jobs.put(("ok", box_px, info))
        except Exception as e:
            jobs.put(("err", box_px, str(e)))

    print("=" * 62)
    print("手势演示套件已启动  ·  右手翻页/取景 + 左手缩放")
    print("-" * 62)
    print("  【右手·翻页】五指张开，%s，%s" % (
        "向左划 = 下一页" if SWIPE_LEFT_IS_NEXT else "向左划 = 上一页",
        "向右划 = 上一页" if SWIPE_LEFT_IS_NEXT else "向右划 = 下一页"))
    print("          食指+中指（其余收拢）划 = 连翻模式，冷却更短，可连续同向翻")
    print("          反向要等手停 0.2 秒：挥手后的自然回位仍会被吞掉")
    print("          方向反了就改 SWIPE_LEFT_IS_NEXT，或设 HAND_OCR_SWIPE_FLIP=1")
    print("  【右手·握拳】面板开着 → 关面板；面板关着 → 退出放映（Esc）")
    print("          2.5 秒内再抓一次 → 回到桌面")
    print("  【右手·复原】关掉面板后 6 秒内，张开手掌停住 %.1f 秒 → 原样叫回来"
          % RESTORE_DWELL)
    print("  【左手·缩放】握拳 = 缩小页面（Ctrl+-）；张开手掌停住 = 放大（Ctrl+=）")
    print("          每次手势走一步，重复请把手放下再抬起")
    print("  左右手判定：%s（预览窗左上角 HAND: L/R 可核对；反了就设 HAND_SIDE_SWAP=%d）"
          % ("已交换" if SIDE_SWAP else "不交换", 0 if SIDE_SWAP else 1))
    print("  【解读】双手各伸食指+中指收拢比框 → 稳住 1 秒 → 自动识别")
    print("          取景框放大倍数 %.2f：在预览窗按 ] 放大 / [ 缩小（会自动记住）"
          % FRAME_ZOOM)
    print("  【退出】在摄像头预览窗按 q（预览窗左上角会显示 HAND: L/R）")
    print("-" * 62)
    print("  大模型：" + llm_status())
    print("  按键只发给前台窗口：启动后 %.0f 秒内点一下 PPT 放映画面" % ARM_DELAY)
    print("=" * 62)

    t0 = time.time()
    last_ts = 0
    fail = 0
    arm_at = time.time() + ARM_DELAY

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                fail += 1
                if fail > 60:
                    print("连续读不到摄像头画面，退出。")
                    print("  多半是摄像头被其他程序抢走了（微信/Zoom/相机），关掉后重开本程序。")
                    break
                continue
            fail = 0
            frame = cv2.flip(frame, 1)          # 镜像：手往右移，框也往右移
            h, w = frame.shape[:2]
            now = time.time()

            ts = int((now - t0) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts
            res = landmarker.detect_for_video(
                mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)), ts)
            # 关键点 + 左右手标签一起过滤，保证 hands[i] 与 sides[i] 一一对应
            hands, sides = [], []
            for i, hd in enumerate(res.hand_landmarks or []):
                if hand_ratios(hd)[1] < MIN_PALM:
                    continue
                hands.append(hd)
                nm = ""
                try:
                    nm = res.handedness[i][0].category_name
                except Exception:
                    pass
                sides.append(norm_side(nm, hd))
            n_hand = len(hands)
            rh = hands[sides.index("R")] if "R" in sides else None      # 右手：翻页/关/复原
            lh = hands[sides.index("L")] if "L" in sides else None      # 左手：缩小/放大
            if n_hand == 1 and rh is None and lh is None:
                rh = hands[0]          # 判不出左右时，退化为"唯一那只手当右手"，别让功能全废
            side_txt = "R" if rh is not None else ("L" if lh is not None else "-")

            # ---------- 取景框跟踪（双手优先）----------
            box = detect_frame_box(hands)
            fb = tracker.update(box, now)
            box_px = box_to_screen(tracker.box, SW, SH) if tracker.box else None

            # ---------- 单手滑动翻页（只认右手）----------
            # 双手在场时不判翻页：否则框选过程中手臂一横移就会误翻页
            if n_hand >= 2:
                slide.lock(now)
            elif n_hand == 1 and (ANY_HAND_PAGING or rh is not None):
                # 五指张开 = 单次翻页；食指+中指 = 连翻（冷却更短，可连续同向翻）
                hand0 = hands[0]          # 任何单手都行，不再要求是右手
                fast = is_two_finger(hand0)
                act = slide.update(hand0, now, fast=fast)
                if act and ENABLE_SLIDE and now >= arm_at:
                    if state["panel"]:            # 翻页后旧面板内容已不符，收掉
                        state["closed_panel"] = {"box": state["panel_box"],
                                                 "info": state["panel"]}
                        state["closed_at"] = now
                        overlay.hide_panel()
                        state["panel"] = None
                    target = "right" if act == "next" else "left"
                    press_key(target)
                    state["status"] = "PAGE: " + act.upper()
                    state["status_t"] = now
                    print("[SLIDE] %s -> press %s%s" % (
                        act, target, "  (2finger fast)" if fast else ""))
                    beep(880, 40)
            else:
                slide.reset(now)

            # ---------- 右手握拳：面板开着就关面板，否则退出放映 ----------
            # 双手同时握拳不参与，避免取景时两手一起收被当成"抓取"
            # 左手握拳 = 缩小页面，不能落到 关闭/退放映 分支里
            left_fist = (ENABLE_LEFT_ZOOM and n_hand == 1 and lh is not None
                         and is_fist(lh))
            fist_hand = (hands[0] if (ANY_HAND_PAGING and n_hand == 1) else rh)
            fist_now = (fist_hand is not None) and (n_hand == 1 or lh is None) \
                and not left_fist and is_fist(fist_hand)
            if fist_now:
                if not state["fist_used"]:
                    state["fist"] += 1
                    if state["fist"] >= FIST_CONFIRM:
                        state["fist_used"] = True
                        if state["panel"]:
                            # 记住这块面板，等下"张开手掌静止"可以原样叫回来
                            state["closed_panel"] = {"box": state["panel_box"],
                                                     "info": state["panel"]}
                            state["closed_at"] = now
                            state["fist_lock"] = now + FIST_LOCK_AFTER_CLOSE
                            overlay.hide_panel()
                            state["panel"] = None
                            state["status"] = "PANEL CLOSED"
                            state["status_t"] = now
                            if ENABLE_RESTORE:
                                print("[FIST] 面板已关闭，%d 秒内张开手掌静止可复原"
                                      % int(RESTORE_WINDOW))
                            beep(520, 60)
                        elif ENABLE_EXIT_PPT and now >= arm_at \
                                and now >= state["fist_lock"]:
                            if now - state["last_grab"] <= FIST_RESET:
                                state["grab_count"] += 1
                            else:
                                state["grab_count"] = 1
                            state["last_grab"] = now
                            if state["grab_count"] >= 2:
                                press_hotkey("win", "d")
                                state["grab_count"] = 0
                                state["status"] = "GRAB x2 -> SHOW DESKTOP"
                            else:
                                press_key("esc")
                                state["status"] = "GRAB -> EXIT SLIDESHOW"
                            state["status_t"] = now
                            print("[FIST] %s" % state["status"])
                            beep(660, 80)
            else:
                state["fist"] = 0
                state["fist_used"] = False      # 松手后解锁，下次握拳才有效

            # ---------- 右手复原：五指张开+静止 → 把刚关掉的面板原样叫回来 ----------
            # 只在"刚关掉面板 RESTORE_WINDOW 秒内、当前没有面板"时有效；
            # 要求静止 RESTORE_DWELL 秒，所以正常翻页（横移）不会误触。
            rest_hand = (hands[0] if (ANY_HAND_PAGING and n_hand == 1) else rh)
            if ENABLE_RESTORE and rest_hand is not None and state["closed_panel"] \
                    and not state["panel"] and not state["busy"] \
                    and (now - state["closed_at"]) <= RESTORE_WINDOW:
                if restore.update(rest_hand, now):
                    cp = state["closed_panel"]
                    overlay.show_panel(cp["box"], cp["info"])
                    state["panel"] = cp["info"]
                    state["panel_box"] = cp["box"]
                    state["closed_panel"] = None
                    state["status"] = "RESTORED"
                    state["status_t"] = now
                    restore.reset()
                    beep(760, 60)
                    print("[RESTORE] 面板已复原（右手张开手掌静止）")
            else:
                restore.reset()

            # 超过时限就不该再复原了，顺手释放以免误触
            if state["closed_panel"] and (now - state["closed_at"]) > RESTORE_WINDOW:
                state["closed_panel"] = None

            # ---------- 左手：握拳缩小 / 张开静止放大 ----------
            # 一次手势只走一步，重复需要把手放下再抬起（避免按住不放把页面缩没了）
            # 只在"画面里只有这一只手"时才认左手缩放：双手在场是取景的场景，
            # 不该因为左手入镜就顺手缩放页面。
            if ENABLE_LEFT_ZOOM and lh is not None and n_hand == 1 \
                    and not tracker.box:
                if is_fist(lh):
                    lstate["open_latch"] = False
                    lstate["fist"] += 1
                    if lstate["fist"] >= FIST_CONFIRM and now - lstate["last"] >= ZOOM_COOLDOWN:
                        lstate["last"] = now
                        press_hotkey(*ZOOM_MINUS_KEY)
                        state["status"] = "ZOOM OUT"
                        state["status_t"] = now
                        print("[ZOOM] 左手握拳 -> 缩小页面 %s" % (ZOOM_MINUS_KEY,))
                        beep(680, 40)
                else:
                    lstate["fist"] = 0
                    if is_open_palm(lh):
                        if not lstate["open_latch"] and \
                                now - lstate["last"] >= ZOOM_COOLDOWN and \
                                lzoom.update(lh, now):
                            lstate["last"] = now
                            lstate["open_latch"] = True
                            press_hotkey(*ZOOM_PLUS_KEY)
                            state["status"] = "ZOOM IN"
                            state["status_t"] = now
                            print("[ZOOM] 左手张开静止 -> 放大页面 %s" % (ZOOM_PLUS_KEY,))
                            beep(1000, 40)
                            lzoom.reset()
                    else:
                        lstate["open_latch"] = False
                        lzoom.reset()
            else:
                lstate["fist"] = 0
                lstate["open_latch"] = False
                lzoom.reset()

            # ---------- 触发识别 ----------
            if fb == "fire" and box_px and not state["busy"] \
                    and (now - state["last_fire"]) > COOLDOWN:
                state["busy"] = True
                state["last_fire"] = now
                state["status"], state["status_t"] = "OCR RUNNING...", now
                # 先把浮层清掉，免得 OCR 把自家的取景框和面板也读进去
                overlay.clear_frame()
                overlay.hide_panel()
                overlay.set_hint("")
                overlay.pump()
                x0, y0, x1, y1 = box_px
                x0c, y0c = max(0, x0), max(0, y0)
                x1c, y1c = min(SW, x1), min(SH, y1)
                if x1c - x0c < 8 or y1c - y0c < 8:
                    jobs.put(("err", box_px, "box too small"))
                else:
                    shot = ImageGrab.grab(bbox=(x0c, y0c, x1c, y1c))
                    threading.Thread(target=recognize, args=(shot, box_px),
                                     daemon=True).start()

            # ---------- 接收 OCR 结果 ----------
            while True:
                try:
                    kind, bpx, payload = jobs.get_nowait()
                except queue.Empty:
                    break
                state["busy"] = False
                if kind == "ok":
                    overlay.show_panel(bpx, payload)
                    state["panel"] = payload
                    state["panel_box"] = bpx
                    state["closed_panel"] = None      # 来了新面板，旧的就没必要复原了
                    llm_ms = payload.get("_llm_ms", 0)
                    state["status"] = "OK: %d lines, %dms%s" % (
                        payload.get("_n", 0), payload.get("_ms", 0),
                        ("  [qwen %dms]" % llm_ms) if llm_ms else "")
                    state["status_t"] = now
                    print("[OCR] %d 行, %dms -> %s | %s" % (
                        payload.get("_n", 0), payload.get("_ms", 0),
                        payload.get("title", ""), payload.get("_raw", "")[:50]))
                    beep(1100, 70)
                else:
                    state["status"] = "FAIL: " + str(payload)[:40]
                    state["status_t"] = now
                    print("[ERR]", payload)

            # ---------- 浮层绘制 ----------
            if tracker.box and box_px:
                overlay.draw_frame(box_px, tracker.progress)
                if state["busy"]:
                    overlay.set_hint("正在识别框内文字…", C_OK)
                elif fb == "holding" and tracker.progress < 1:
                    overlay.set_hint("稳住别动 %d%%" % int(tracker.progress * 100))
                else:
                    overlay.set_hint("")
            else:
                overlay.clear_frame()          # 没框就把框清掉
                if not state["panel"] and not state["busy"]:
                    overlay.set_hint("单手左右划＝翻页 · 双手框选＝识别 · 握拳＝关面板/退出放映")
                else:
                    overlay.set_hint("")
            overlay.pump()

            # ---------- 摄像头预览 ----------
            for hd in hands:
                px = [(int(lm.x * w), int(lm.y * h)) for lm in hd]
                for a, b in HAND_CONNECTIONS:
                    cv2.line(frame, px[a], px[b], (0, 255, 0), 2)
                for p in px:
                    cv2.circle(frame, p, 3, (0, 0, 255), -1)
            if box:
                bx0, by0 = int(box[0] * w), int(box[1] * h)
                bx1, by1 = int(box[2] * w), int(box[3] * h)
                cv2.rectangle(frame, (bx0, by0), (bx1, by1), (255, 229, 0), 2)

            if now < arm_at:                    # 倒计时：先让用户点一下 PPT 窗口
                msg = "READY IN %.1fs  --  click the PPT slideshow window now" % (arm_at - now)
                color = (0, 200, 255)
            elif now - state["status_t"] < 3.0:
                msg = state["status"]
                color = (0, 255, 255)
            else:
                msg = "R-hand: swipe=paging fist=close palm-hold=restore | L-hand: fist=zoom out palm-hold=zoom in"
                color = (0, 255, 255)
            cv2.putText(frame, msg, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
            # 左上角显示识别到的左右手，方便确认 1 号位是不是"你的右手"
            cv2.putText(frame, "HAND: %s" % side_txt, (10, 46),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 120) if side_txt != "-" else (90, 90, 90), 2)
            cv2.putText(frame, "hands=%d hold=%.2f zoom=%.2f slide=%s why=%s v=%.2f %s swap=%d" % (
                n_hand, tracker.progress, FRAME_ZOOM, (slide.dir or "-"),
                (slide.why or "-"), slide.speed,
                ("BLK:" + slide.block_dir) if slide.block_dir else "free",
                1 if SIDE_SWAP else 0),
                (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (120, 255, 120) if not slide.block_dir else (200, 200, 200), 1)

            scale = PREVIEW_W / float(w)
            small = cv2.resize(frame, (PREVIEW_W, int(h * scale)))
            cv2.imshow("Gesture Suite", small)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            # [ ] 实时调取景框放大倍数：框够不到屏幕边缘就按 ]，抖/停不准就按 [
            if key in (ord("["), ord("]")):
                d = FRAME_ZOOM_STEP if key == ord("]") else -FRAME_ZOOM_STEP
                z = min(FRAME_ZOOM_MAX, max(FRAME_ZOOM_MIN, FRAME_ZOOM + d))
                if abs(z - FRAME_ZOOM) > 1e-6:
                    FRAME_ZOOM = z
                    save_frame_zoom(FRAME_ZOOM)
                    state["status"] = "ZOOM %.2f" % FRAME_ZOOM
                    state["status_t"] = now
                    print("[ZOOM] %.2f  (已记到 frame.json)" % FRAME_ZOOM)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if overlay:
            overlay.close()
        landmarker.close()
        print("已退出")


if __name__ == "__main__":
    main()
