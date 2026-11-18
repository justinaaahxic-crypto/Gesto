# -*- coding: utf-8 -*-
"""不开摄像头，直接验证结果面板的排版：译文块 + 讲解块是否重叠、是否超出屏幕。

用一个隐藏的 Tk 画布真跑 Overlay.show_panel()，量所有 panel 图元的实际包围盒。
"""
import os
import sys
import types
import tkinter as tk
import tkinter.font as tkfont

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE))
import hand_frame_ocr as H       # noqa: E402

FAM = "Microsoft YaHei"


class Fake:
    pass


def render(info, sw=1920, sh=1080, box=(120, 120, 520, 420), title="panel"):
    root = tk.Tk()
    root.withdraw()
    o = Fake()
    o.sw, o.sh, o.fam = sw, sh, FAM
    o.canvas = tk.Canvas(root, width=sw, height=sh, bg="#101010")
    o.tkfont = tkfont
    o.f_body = tkfont.Font(family=FAM, size=13)
    o.f_small = tkfont.Font(family=FAM, size=10)
    o._tfonts = {}
    o._panel_key = None
    o._round_rect = lambda x0, y0, x1, y1, r, **kw: o.canvas.create_rectangle(
        x0, y0, x1, y1, outline=kw.get("outline", ""), fill=kw.get("fill", ""),
        tags="panel")
    for name in ("show_panel", "_ls", "_title_font"):
        setattr(o, name, types.MethodType(getattr(H.Overlay, name), o))

    o.show_panel(box, info)
    bb = o.canvas.bbox("panel") or (0, 0, 0, 0)
    items = len(o.canvas.find_withtag("panel"))
    root.destroy()
    return bb, items


EN_INFO = {
    "title": "特征值", "en": "Eigenvalue", "tag": "DeepSeek · deepseek-chat",
    "trans": "矩阵 A 的特征值是一个标量 lambda，使得对于某个非零向量 v，有 Av = lambda v。",
    "body": ("这句话给出的是特征值的定义：存在一个非零向量 v，使矩阵 A 作用在 v 上得到的结果，"
             "恰好等于把 v 按某个标量 lambda 拉伸或压缩。\n\n"
             "关键词：eigenvalue（特征值）；matrix A（方阵）；nonzero vector v（非零向量）"),
    "extra": "另匹配到：矩阵",
    "footer": "识别文本：The eigenvalue of a matrix A is a scalar lambda such that Av = lambda v",
}

ZH_INFO = {
    "title": "拉格朗日乘子", "en": "Lagrange Multiplier", "tag": "DeepSeek · deepseek-chat",
    "trans": "",
    "body": "拉格朗日乘子法把带约束的极值问题转成无约束问题：构造 L(x,λ)=f(x)+λ·g(x)，"
            "对 x 和 λ 求偏导令其为零。λ 的经济含义是约束的影子价格。",
    "extra": "", "footer": "识别文本：拉格朗日乘子法",
}

LONG_ZH = dict(ZH_INFO)
LONG_ZH["body"] = "这是一段很长的讲解。" * 70        # 压力测试：840 字，看会不会溢出屏幕

for name, info, sh in (("英文（有译文）", EN_INFO, 1080),
                       ("中文（无译文）", ZH_INFO, 1080),
                       ("英文（有译文）@768p", EN_INFO, 768),
                       ("超长讲解 @1080p", LONG_ZH, 1080),
                       ("超长讲解 @768p", LONG_ZH, 768),
                       ("超长讲解+长原文 @768p", dict(LONG_ZH, footer="识别文本：" + "very long english raw text " * 6), 768)):
    bb, n = render(info, sh=sh)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    verdict = "OK" if bb[1] >= 0 and bb[3] <= sh else "溢出！"
    print("%-22s 屏高%4d  面板 %4dx%4d  图元%2d  y=[%d,%d]  %s"
          % (name, sh, w, h, n, bb[1], bb[3], verdict))
