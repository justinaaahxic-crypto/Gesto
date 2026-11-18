# -*- coding: utf-8 -*-
"""复原手势（张开手掌静止 → 叫回面板）的判定回归。

    .venv\\Scripts\\python.exe hand-ocr\\tests\\restore_test.py

构造合成手部关键点，验三件事：
  1) 张开手静止 >= RESTORE_DWELL 秒 → 判定复原
  2) 张开手横移（正在翻页）→ 不能判定复原
  3) 握拳 / 只伸三指 → 不能判定复原
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hand_frame_ocr as H  # noqa: E402


class P:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = x, y


def make_hand(cx=0.5, cy=0.75, ratios=(1.5, 1.5, 1.5, 1.5), palm=0.16):
    """按给定四指比值造 21 个点。ratios=四指 |指尖-腕|/|指根-腕|。"""
    pts = [P(cx, cy)] * 21
    wrist = P(cx, cy)
    pts[0] = wrist
    pts[9] = P(cx, cy - palm)                      # 中指根，决定手掌尺寸
    for i, (tip, pip, a) in enumerate(zip((8, 12, 16, 20), (6, 10, 14, 18),
                                          (-0.45, -0.15, 0.15, 0.45))):
        r_pip = palm * 0.9 if i == 1 else palm * 0.8
        r_tip = r_pip * ratios[i]
        pts[pip] = P(cx + r_pip * math.sin(a), cy - r_pip * math.cos(a))
        pts[tip] = P(cx + r_tip * math.sin(a), cy - r_tip * math.cos(a))
    # 拇指随便给两个点，比值判定用不到
    pts[4] = P(cx - 0.1, cy - 0.05)
    pts[2] = P(cx - 0.05, cy)
    return pts


def run(hand_fn, seconds, fps=30, jitter=0.0):
    t = H.RestoreTracker()
    fired_at = None
    dt = 1.0 / fps
    for i in range(int(seconds * fps) + 1):
        now = i * dt
        hand = hand_fn(now)
        if t.update(hand, now):
            fired_at = now
            break
    return fired_at


def main():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = (got == want)
        ok = ok and good
        print("  %-40s %s" % (name, "通过" if good else "失败（实得 %r，应为 %r）" % (got, want)))

    # 1) 静止张开手：应该在 >=0.8s 才触发
    fired = run(lambda t: make_hand(), 2.0)
    check("张开手静止 → 触发复原", fired is not None, True)
    check("触发时刻 >= RESTORE_DWELL", (fired or 0) >= H.RESTORE_DWELL - 0.06, True)
    print("       实测触发时刻 %.2fs（阈值 %.2fs）" % (fired or -1, H.RESTORE_DWELL))

    # 2) 张开手横移（类似翻页）：不应触发
    fired = run(lambda t: make_hand(cx=0.35 + 0.4 * t), 2.0)
    check("张开手横移 → 不触发", fired is None, True)

    # 3) 握拳：不应触发
    fired = run(lambda t: make_hand(ratios=(0.75, 0.75, 0.75, 0.75)), 2.0)
    check("握拳静止 → 不触发", fired is None, True)

    # 4) 只伸三指（取景手势附近）：不应触发
    fired = run(lambda t: make_hand(ratios=(1.6, 0.8, 0.8, 0.8)), 2.0)
    check("只伸一指/三指 → 不触发", fired is None, True)

    # 5) 手抖（小幅度抖动 < STILL_RADIUS）：应仍触发
    fired = run(lambda t: make_hand(cx=0.5 + 0.01 * math.sin(t * 20)), 2.0)
    check("轻微抖动（±0.01）→ 仍触发", fired is not None, True)

    print("\n%s" % ("全部通过" if ok else "存在失败项，需修"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
