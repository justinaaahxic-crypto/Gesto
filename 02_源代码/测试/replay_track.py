# -*- coding: utf-8 -*-
"""用抽出来的轨迹 CSV 离线重放 SlideTracker，验证"左滑是否终于能翻到下一页"。
把视频坐标取负，等价于主循环里的 cv2.flip 镜像（只用差分量）。
"""
import csv
import os
import sys
BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE))
import hand_frame_ocr as H          # noqa: E402


class P:
    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def replay(rows, flip=True, left_is_next=True):
    H.SWIPE_LEFT_IS_NEXT = left_is_next
    cur = {"rs": None, "palm": 0.0}
    orig = H.hand_ratios
    H.hand_ratios = lambda h: (cur["rs"], cur["palm"])   # 用 CSV 里的真实比值
    tr = H.SlideTracker()
    out = []
    for r in rows:
        t = float(r["t"])
        if r["nhand"] != "1":
            tr.reset()
            tr.update(None, t)
            continue
        cx = float(r["cx"])
        if flip:
            cx = -cx
        cy = float(r["cy"])
        cur["rs"] = [float(r["r0"]), float(r["r1"]), float(r["r2"]), float(r["r3"])]
        cur["palm"] = float(r["palm"])
        hand = [P(cx, cy) for _ in range(21)]
        a = tr.update(hand, t)
        if a:
            out.append((t, a, tr.why))
    H.hand_ratios = orig
    return out


rows = list(csv.DictReader(open(os.path.join(BASE, "data", "track_raw.csv"))))
print("轨迹帧数 %d（其中单手 %d 帧）" % (
    len(rows), sum(1 for r in rows if r["nhand"] == "1")))

for name, kw in (("旧方向(dx>0→next)", dict(left_is_next=False)),
                 ("新方向(左划→next)", dict(left_is_next=True))):
    acts = replay(rows, **kw)
    print("\n--- %s ---" % name)
    print("  触发 %d 次：%s" % (len(acts), acts))
