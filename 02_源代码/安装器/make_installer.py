# -*- coding: utf-8 -*-
"""把 release/Gesto 打成一个单文件安装程序（含卸载器）。

    .venv\\Scripts\\python.exe tools\\make_installer.py

产物：`Gesto_Setup_1.0.4.exe`（约 175 MB），双击即可安装，无需管理员权限。
内部结构：
    PyInstaller 单文件外壳（tkinter 安装界面）
      + payload.zip        ← release/Gesto 的全部内容（约 160 MB）
      + uninstall_stub.exe ← 卸载器，安装时复制成 <安装目录>/Uninstall.exe
      + logo.ico / logo_128.png
"""
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
PAYLOAD_DIR = os.environ.get("GESTO_PAYLOAD_DIR") or \
    os.path.join(ROOT, "release", "Gesto")
WORK = os.path.join(ROOT, "build", "setup")
ASSETS = os.path.join(ROOT, "gesto", "assets")
VERSION = "1.0.4"
OUT_NAME = "Gesto_Setup_%s.exe" % VERSION


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def make_payload():
    """把 release/Gesto 压成 payload.zip（不动原目录）。"""
    os.makedirs(WORK, exist_ok=True)
    out = os.path.join(WORK, "payload.zip")
    doc = os.path.join(HERE, "使用说明.txt")
    # 缓存判据要同时看 Gesto.exe 和说明书——只看 exe 的话，改了说明书会被复用旧包
    newest = os.path.getmtime(os.path.join(PAYLOAD_DIR, "Gesto.exe"))
    if os.path.exists(doc):
        newest = max(newest, os.path.getmtime(doc))
    if os.path.exists(out) and os.path.getmtime(out) > newest:
        log("复用已有 payload.zip")
        return out
    log("打包 payload.zip …")
    t = time.time()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as z:
        doc = os.path.join(HERE, "使用说明.txt")
        for r, _d, fs in os.walk(PAYLOAD_DIR):
            for f in fs:
                p = os.path.join(r, f)
                arc = os.path.relpath(p, PAYLOAD_DIR)
                if arc == "使用说明.txt" and os.path.exists(doc):
                    continue        # 使用说明以 tools/ 里的版本为准
                z.write(p, arc)
                n += 1
        if os.path.exists(doc):
            z.write(doc, "使用说明.txt")     # PyInstaller 重建 release 时不会带手写文件
            n += 1
    log("  %d 个文件 → %.0f MB，用时 %.0f 秒" % (
        n, os.path.getsize(out) / 1e6, time.time() - t))
    return out


def build_stub(script, name, extra, dist):
    """跑一次 PyInstaller 单文件打包。"""
    cmd = [PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--windowed", "--name", name,
           "--distpath", dist,
           "--workpath", os.path.join(WORK, "w_" + name),
           "--specpath", os.path.join(WORK, "s_" + name),
           "--icon", os.path.join(ASSETS, "logo.ico")]
    for item in extra:
        cmd += ["--add-data", item]
    cmd.append(script)
    log("打包 %s …" % name)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    exe = os.path.join(dist, name + ".exe")
    if r.returncode != 0 or not os.path.exists(exe):
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit("打包 %s 失败" % name)
    log("  %s %.1f MB" % (os.path.basename(exe), os.path.getsize(exe) / 1e6))
    return exe


def main():
    if not os.path.isdir(PAYLOAD_DIR):
        raise SystemExit("找不到 %s，先跑 PyInstaller 生成 release" % PAYLOAD_DIR)

    # 安装界面要用的图
    from PIL import Image
    logo128 = os.path.join(WORK, "logo_128.png")
    os.makedirs(WORK, exist_ok=True)
    Image.open(os.path.join(ASSETS, "logo.png")).resize(
        (128, 128), Image.LANCZOS).save(logo128)
    logo_ico = os.path.join(ASSETS, "logo.ico")

    payload = make_payload()

    # 1) 卸载器（小，无 payload）
    un_dist = os.path.join(WORK, "dist_un")
    un_exe = build_stub(os.path.join(HERE, "uninstall_app.py"), "Uninstall",
                        ["%s;." % logo_ico], un_dist)
    # 打成安装包里的固定名字，安装时由 setup 复制成 <安装目录>\Uninstall.exe
    un_stub = os.path.join(WORK, "uninstall_stub.exe")
    shutil.copyfile(un_exe, un_stub)

    # 2) 安装器（把 payload 与卸载器都塞进去）
    st_dist = os.path.join(WORK, "dist_st")
    setup_exe = build_stub(
        os.path.join(HERE, "setup_app.py"), "Gesto_Setup",
        ["%s;." % payload,
         "%s;." % un_stub,
         "%s;." % logo_ico,
         "%s;." % logo128],
        st_dist)

    final = os.path.join(ROOT, OUT_NAME)
    shutil.copyfile(setup_exe, final)
    log("")
    log("安装包：%s" % final)
    log("  大小 %.1f MB" % (os.path.getsize(final) / 1e6))
    log("  内含 %d 个文件（payload %.0f MB + 卸载器 %.1f MB）" % (
        sum(len(f) for _r, _d, f in os.walk(PAYLOAD_DIR)),
        os.path.getsize(payload) / 1e6, os.path.getsize(un_exe) / 1e6))


if __name__ == "__main__":
    main()
