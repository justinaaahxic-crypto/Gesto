# -*- coding: utf-8 -*-
"""新规则回归：①快速反向（左滑完马上右滑）不再被吞 ②同向连挥不变三次 ③食指+中指连翻模式。

    .venv\\Scripts\\python.exe hand-ocr\\tests\\regress_fast.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hand_frame_ocr as H  # noqa: E402


class P:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = x, y


PALM = [1.35, 1.40, 1.42, 1.38]      # 五指张开
TWO = [1.55, 1.60, 0.80, 0.78]       # 食指+中指伸出，无名指+小指收拢


def run(keys, ratios, fps=30.0):
    H.hand_ratios = lambda h: (ratios, 0.12)
    tr = H.SlideTracker()
    cx, cy = 0.5, 0.5
    acts, t = [], 0.0
    dt = 1.0 / fps
    for dur, (vx, vy) in keys:
        for _ in range(max(1, int(round(dur * fps)))):
            cx += vx * dt
            cy += vy * dt
            t += dt
            a = tr.update([P(cx, cy) for _ in range(21)], t, fast=(ratios is TWO))
            if a:
                acts.append((round(t, 2), a))
    return acts


def sw(d=0.55, sign=-1, v=1.6):
    return (d, (sign * v, 0))


def home(d=0.55, sign=1, v=1.6):
    return (d, (sign * v, 0))


def still(d):
    return (d, (0, 0))


CASES = [
    ("① 左挥·停0.28s·右挥（用户报的「两次变一次」）",
     [sw(), home(), still(0.28), sw(sign=1)], PALM, ["next", "prev"]),
    ("② 左挥·回位（只停0.10s）·右挥 → 回位必须被吞",
     [sw(), home(), still(0.10), sw(sign=1)], PALM, ["next"]),
    ("③ 同向连挥两次（五指）→ 不能变三次",
     [sw(), home(), still(0.05), sw(), home()], PALM, ["next", "next"]),
    ("④ 一次长挥 1.1s（五指）→ 只翻一页",
     [(1.1, (-1.6, 0))], PALM, ["next"]),
    ("⑤ 食指中指·左挥·回位·左挥 → 连翻两页",
     [sw(), home(), still(0.05), sw(), home()], TWO, ["next", "next"]),
    ("⑥ 食指中指·一次长挥 1.1s → 仍然只翻一页",
     [(1.1, (-1.6, 0))], TWO, ["next"]),
    ("⑦ 食指中指·左挥·停0.28s·右挥 → 快速反向也成立",
     [sw(), home(), still(0.28), sw(sign=1)], TWO, ["next", "prev"]),
]


def main():
    ok = 0
    for name, keys, ratios, want in CASES:
        got = [a for _t, a in run(keys, ratios)]
        good = got == want
        ok += good
        print("%s %-46s 期望 %-22s 实得 %-22s %s" % (
            "PASS" if good else "FAIL", name, want, got,
            "" if good else "  <-- 不符"))
    print("\n%d/%d 通过" % (ok, len(CASES)))
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
