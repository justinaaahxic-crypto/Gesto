# -*- coding: utf-8 -*-
"""合成运动轨迹跑回归：验证"左滑能翻页"的同时，老的误判（回位被当成反向、一次长挥连发）没复现。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hand_frame_ocr as H          # noqa: E402


class P:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def run(keys, fps=30.0, open_hand=True):
    """keys: [(持续时间, (dx,dy)/s 速度), ...] 依次执行，返回触发的动作列表。"""
    rs = [1.35, 1.40, 1.42, 1.38] if open_hand else [0.7, 0.7, 0.7, 0.7]
    H.hand_ratios = lambda h: (rs, 0.12)
    tr = H.SlideTracker()
    cx, cy = 0.5, 0.5
    acts = []
    t = 0.0
    dt = 1.0 / fps
    for dur, (vx, vy) in keys:
        n = max(1, int(round(dur * fps)))
        for _ in range(n):
            cx += vx * dt
            cy += vy * dt
            t += dt
            a = tr.update([P(cx, cy) for _ in range(21)], t)
            if a:
                acts.append((round(t, 2), a))
    return acts


def sw(d=0.55, sign=-1, v=1.6):
    """一次挥动：sign=-1 向左（画面 x 减小），+1 向右。"""
    return (d, (sign * v, 0))


def home(d=0.55, sign=1, v=1.6):
    """回位（幅度与挥动同量级）。"""
    return (d, (sign * v, 0))


def still(d):
    return (d, (0, 0))


CASES = [
    ("①一次左挥+回位",
     [sw(), home()], ["next"]),
    ("②左挥·回位·左挥（停 0.05s）",
     [sw(), home(), still(0.05), sw(), home()], ["next", "next"]),
    ("③连挥三次",
     [sw(), home(), still(0.05), sw(), home(), still(0.05), sw(), home()],
     ["next", "next", "next"]),
    ("④左挥·停 0.8s·右挥（反向翻回）",
     [sw(), home(), still(0.8), (0.55, (1.6, 0)), home(d=0.55, sign=-1, v=1.6)],
     ["next", "prev"]),
    ("⑤左挥后立刻右挥（回位，必须吞掉）",
     [sw(), (0.55, (1.6, 0))], ["next"]),
    ("⑥左挥后 0.15s 内右挥（回位，必须吞掉）",
     [sw(), still(0.15), (0.55, (1.6, 0))], ["next"]),
    ("⑦慢速长挥 1.2s",
     [(1.2, (-0.45, 0))], ["next"]),
    ("⑧原地抖动 ±0.05 共 4s",
     [seg for _ in range(8) for seg in ((0.25, (0.2, 0)), (0.25, (-0.2, 0)))], []),
    ("⑨竖直抬手",
     [(0.55, (0, -1.6))], []),
    ("⑩45 度斜挥（左+上）",
     [(0.55, (-1.15, -1.15))], ["next"]),
    ("⑪斜挥（左+下）",
     [(0.55, (-1.15, 1.15))], ["next"]),
    ("⑫小幅右挥（0.12）",
     [(0.4, (0.30, 0))], ["prev"]),
    ("⑬握拳划过（非张开手）",
     [sw()], []),
]

ok = 0
for name, keys, want in CASES:
    open_hand = "握拳" not in name
    got = [a for _, a in run(keys, open_hand=open_hand)]
    good = got == want
    ok += good
    print("%s %-28s 期望 %-18s 实得 %-18s" % (
        "PASS" if good else "FAIL", name, want, got))
print("\n%d/%d 通过" % (ok, len(CASES)))
