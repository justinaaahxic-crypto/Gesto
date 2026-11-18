# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本 —— 把 Gesto 做成不依赖 Python 的独立程序。

    .venv\\Scripts\\python.exe -m PyInstaller gesto\\Gesto.spec --noconfirm

产物：dist/Gesto/Gesto.exe（onedir，整个文件夹一起分发）
      同一个 exe 支持 `Gesto.exe --engine`，UI 就是这样拉起手势引擎的。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

HERE = os.path.abspath(SPECPATH)           # .../gesto
ROOTD = os.path.dirname(HERE)              # 项目根

datas, binaries, hiddenimports = [], [], []

# mediapipe / rapidocr / onnxruntime 都有模型、配置和编译扩展，必须整包收
for pkg in ("mediapipe", "rapidocr_onnxruntime", "onnxruntime"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

datas += [
    (os.path.join(HERE, "assets"), "assets"),                       # _MEIPASS/assets
    (os.path.join(ROOTD, "hand-ocr", "glossary.json"), "."),        # _MEIPASS/glossary.json
    (os.path.join(ROOTD, "gesture-demo", "hand_landmarker.task"),
     "gesture-demo"),                                               # _MEIPASS/gesture-demo/
]

hiddenimports += ["hand_frame_ocr"]

a = Analysis(
    [os.path.join(HERE, "app.py")],
    pathex=[HERE, os.path.join(ROOTD, "hand-ocr")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # 别排除 matplotlib：mediapipe 的 drawing_utils 在 import 链上会用到它
    excludes=["PyQt5", "PySide2", "PySide6", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Gesto",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # GUI 程序，不要黑框
    disable_windowed_traceback=False,
    icon=os.path.join(HERE, "assets", "logo.ico"),
    version=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Gesto",
)
