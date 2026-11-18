# Gesto — 手势演示控制台

面向 Windows 课堂演示的摄像头手势工具：用手势翻页、关闭或复原解读面板、缩放页面，以及框选屏幕文字进行 OCR 和可选的大模型解读。

当前交付版本为 **1.0.4**。本仓库从原成果包中整理出技术报告、源代码、软件及网页四个目录，保留各目录内部结构。

> 仓库：[justinaaahxic-crypto/Gesto](https://github.com/justinaaahxic-crypto/Gesto) · 维护者：Justin。Python 与依赖兼容版本尚未完成验证。

## 功能与定位

- **手势翻页**：五指张开横向滑动；食指与中指伸出时使用冷却更短的连翻模式。
- **减少回位误触**：位移按手掌大小归一化，结合同向回撤复位和反向锁定；反向操作的停顿阈值为 0.20 秒。
- **上下文关闭与复原**：握拳关闭当前解读面板；没有面板时可退出放映，连续握拳可回到桌面。关闭面板后 6 秒内，张掌静止约 0.8 秒可复原。
- **页面缩放**：启用缩放手势时，左手握拳发送 Ctrl+-，张掌静止发送 Ctrl+=；效果取决于前台软件对快捷键的支持。
- **双手取景读屏**：两手食指伸出、中指收拢形成取景框，稳定约 1 秒后截取对应屏幕区域并执行 OCR。
- **离线与联网两种解读方式**：MediaPipe 手部检测、RapidOCR 和术语库在本地运行；整句翻译及详细讲解需要配置兼容接口的 API Key，并联网调用服务。
- **桌面界面**：包含控制台、手势词典、运行日志、设置和关于页面。

这是一份课堂演示研究原型的交付包；不将论文中的实测数字视为对其他设备的性能保证。

## 目录结构

```text
gesto/
├── README.md
├── LICENSE
├── .gitignore
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md
├── .github/             # Issue 与 Pull Request 模板
├── 01_技术报告/         # 报告、源代码附录和演示图片
├── 02_源代码/
│   ├── 核心/
│   │   ├── app.py             # Tkinter 桌面界面
│   │   ├── hand_frame_ocr.py  # 手势、OCR、屏幕浮层与模型调用
│   │   ├── glossary.json      # 离线术语库
│   │   └── Gesto.spec         # PyInstaller 配置
│   ├── 安装器/         # 安装器、卸载器及构建脚本
│   └── 测试/           # 回归、轨迹重放、布局检查与轨迹 CSV
├── 03_软件/            # 使用说明；安装包仅保留在本地，另发 Releases
└── 04_网页版/          # 静态介绍页、logo 和官网截图
```

## 环境与依赖

| 用途 | 要求 |
| --- | --- |
| 使用已打包软件 | 按随包网页说明面向 Windows 10/11；可用摄像头和图形桌面；不需要安装 Python |
| 运行引擎源码 | 带 Tkinter 的 Python；`opencv-python`、`mediapipe`、`numpy`、`Pillow`、`rapidocr-onnxruntime`、`pyautogui` |
| 构建程序 | 另需 `PyInstaller`，以及完整图标、模型和正确的构建路径 |

当前没有依赖锁文件，也没有可据以确认的 Python 兼容范围；以下安装命令按源码导入项整理，未经过干净环境安装验证。

## 安装与快速开始

### 使用 Windows 安装包

1. 使用成果包中的 `03_软件/Gesto_Setup_1.0.4.exe` 安装，随后打开 Gesto。
2. 打开演示文稿并进入放映模式。
3. 在 Gesto 中点击“启动引擎”，在默认 5 秒倒计时内点击放映画面，使其获得焦点。
4. 在摄像头前比划手势；从“运行日志”检查识别和动作输出。

安装包为 179,890,464 字节，超过 GitHub 普通 Git 单文件 100 MiB 限制，应作为 **GitHub Release 附件**分发。本仓库的 `.gitignore` 默认不跟踪该安装包；安装包通过 [GitHub Releases](https://github.com/justinaaahxic-crypto/Gesto/releases) 分发。

### 从源码启动手势引擎

在仓库根目录使用 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install opencv-python mediapipe numpy Pillow rapidocr-onnxruntime pyautogui
$src = Join-Path (Get-Location) '02_源代码'
```

启动前必须补齐 **当前包内不存在** 的 MediaPipe HandLandmarker 模型，保存为：

```text
02_源代码/gesture-demo/hand_landmarker.task
```

从 [MediaPipe Hand Landmarker 文档](https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)获取匹配模型；当前仓库未固定模型版本和校验值；获取模型时请核对其适用许可。该路径来自引擎实际的相对路径解析，模型目录需要自行创建。

补齐模型并安装兼容依赖后运行：

```powershell
.\.venv\Scripts\python.exe (Join-Path $src '核心\hand_frame_ocr.py')
```

在摄像头预览窗按 `q` 退出；终端运行时也可按 Ctrl+C。

**桌面源码与打包限制**：直接运行 `核心/app.py` 尚不能作为完整快速开始。它引用未随包提供的 `核心/assets/`（含 `logo.png`、`hero.png`、`wordmark.png` 等），并将引擎定位到 `02_源代码/hand-ocr/hand_frame_ocr.py`，与实际 `核心/` 目录不一致。`Gesto.spec` 和安装器构建脚本也保留旧目录引用；补齐资源并校正路径后才能验证桌面启动和重新打包。当前文档未修改这些源码。

## 使用示例

| 场景 | 操作与预期行为 |
| --- | --- |
| PPT 翻页 | 单手张掌向左划默认下一页，向右划默认上一页；动作发给前台窗口 |
| 快速连续翻页 | 食指、中指伸出，其余手指收拢后横向挥动；同方向再次触发仍需回撤复位 |
| 放大网页 | 启用左手缩放后，左手张掌并停住；前台浏览器接收 Ctrl+= |
| 识别课件术语 | 双手比框并稳住；识别屏幕文字，未配置 Key 时仅提供本地术语匹配 |
| 翻译英文段落 | 配置服务后重新启动引擎，再框选英文；返回译文、讲解及关键词 |
| 复原解读面板 | 握拳关闭后，在 6 秒内张掌静止约 0.8 秒 |

缩放开启时，左手动作优先用于缩放，不应同时期待其执行握拳退出等动作。左右手几何判定、互换设置及摄像头镜像会影响分工。

## 配置项

打包程序将配置和引擎日志写入 `%LOCALAPPDATA%\Gesto\`。独立运行引擎时默认在 `核心/` 读写配置，也可通过 `GESTO_CFG_DIR` 指定已有的可写目录。

独立引擎的 `config.json` 示例：

```json
{
  "llm": {
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "",
    "model": "qwen-turbo",
    "timeout": 12,
    "max_tokens": 320
  }
}
```

空 Key 表示不启用大模型；`max_tokens` 为基础上限，详细解读请求在代码中至少使用 900。桌面界面的默认服务为 DeepSeek、模型为 `deepseek-chat`，与独立引擎默认值不同；使用桌面版时以设置页为准。

| 环境变量 | 默认值或作用 |
| --- | --- |
| `HAND_OCR_LLM_KEY` | 覆盖 Key；其次读取 `DASHSCOPE_API_KEY` |
| `HAND_OCR_LLM_URL` / `HAND_OCR_LLM_MODEL` | 覆盖服务地址与模型名 |
| `HAND_OCR_ZOOM` | 取景框放大倍数，默认 1.5，范围 1.0–2.5 |
| `HAND_OCR_ARM_DELAY` | 启动倒计时，默认 5 秒，范围 0–30 |
| `HAND_OCR_SWIPE_FLIP` | 设为 `1` 时反转默认翻页方向 |
| `HAND_OCR_ANY_HAND` | 默认 `1`，允许任意单手参与翻页判定 |
| `HAND_OCR_ZOOM_GESTURE` | 默认 `1`，启用左手缩放；`0` 关闭 |
| `HAND_OCR_RESTORE` | 默认启用；`0` 关闭面板复原 |
| `HAND_SIDE_GEOMETRY` | 默认 `1`，使用几何信息判定左右手 |
| `HAND_SIDE_INVERT` / `HAND_SIDE_SWAP` | 均默认 `1`；分别影响几何判定和模型标签路径，桌面设置会同步传入 |
| `GESTO_CFG_DIR` | 独立引擎的配置目录 |

引擎大模型参数的优先级为：环境变量 → `config.json` → 内置默认值。`frame.json` 保存取景放大倍数，预览窗的 `[` / `]` 可实时调整。`glossary.json` 的 `entries` 保存离线术语条目。

启用模型服务后，识别出的文本会发送到所配置的服务。Key 保存在本地明文配置中；当前桌面代码还会把传给引擎的环境参数写到日志视图，其中可能包含 Key，分享日志或截图前应删除这些内容。

## 检查与测试

测试脚本位于 `02_源代码/测试/`，原导入路径与交付目录不完全一致。可在上面定义 `$src` 的同一 PowerShell 会话中临时补充模块路径：

```powershell
$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (@((Join-Path $src '核心'), $oldPythonPath) | Where-Object { $_ }) -join ';'
    .\.venv\Scripts\python.exe (Join-Path $src '测试\regress_swipe.py')
    .\.venv\Scripts\python.exe (Join-Path $src '测试\regress_fast.py')
    .\.venv\Scripts\python.exe (Join-Path $src '测试\restore_test.py')
    .\.venv\Scripts\python.exe (Join-Path $src '测试\replay_track.py')
    .\.venv\Scripts\python.exe (Join-Path $src '测试\panel_layout.py')
} finally {
    $env:PYTHONPATH = $oldPythonPath
}
```

前两份回归脚本分别含 13 和 7 个合成轨迹用例；复原脚本含 5 项检查。轨迹重放使用随包 CSV；布局脚本需要 Tk 图形环境。部分脚本仅打印结果，不能只凭退出码判定全部通过。本次文档整理没有执行这些测试，也未验证摄像头、安装器或联网服务。

## 常见问题

**预览黑屏或没有手势输出？** 检查摄像头是否被占用、桌面应用是否已有相机访问权限。源码中的 `CAM_INDEX` 默认为 `0`，多摄像头设备可能需要调整。

**日志显示翻页，但 PPT 没动？** 模拟按键只发给前台窗口，重新点击放映画面；同时检查方向设置。握拳等动作也会影响当前前台软件。

**左右手反了或握拳变成缩放？** 检查预览中的手别和“左右手判定互换”；不需要缩放时关闭缩放手势。

**框选后没有整句翻译？** 未配置 Key 时只查术语库；配置后保存并重启引擎，确认服务地址、模型名和服务可用性。

**网页图片或下载链接失效？** `04_网页版/index.html` 引用 `screenshot.png`，实际图片名为 `官网截图.png`；安装包链接也指向网页同目录。托管前需要修正图片引用，并将安装包链接改为真实 Release 地址。该页面是介绍页，不会在浏览器中运行 Python 手势引擎。

## 版本与更新记录

当前包和桌面代码标记为 `1.0.4`。仓库未提供可核验的正式发布日期或完整历史，因此不推测各功能首次出现的版本。详见 [CHANGELOG](CHANGELOG.md)。

## 许可证、贡献与联系

- 本项目原创软件源码与网页代码采用 [MIT 许可证](LICENSE)，允许使用、修改、分发及商用，须保留版权和许可声明。
- 论文、技术报告、图表、模型及第三方资源不包含在上述授权范围内。第三方依赖和资源遵循各自的许可证。
- 贡献方式见 [CONTRIBUTING](CONTRIBUTING.md)，社区约定见 [CODE_OF_CONDUCT](CODE_OF_CONDUCT.md)。
- 一般问题通过 [Issues](https://github.com/justinaaahxic-crypto/Gesto/issues) 联系维护者 Justin。尚未公布专用私密联系渠道；请勿在公开 Issue 中发送凭据或其他私人信息。

致谢：MediaPipe、RapidOCR、ONNX Runtime、OpenCV、Pillow、PyAutoGUI 和 PyInstaller。

## 截图

以下为随包已有的演示图片。

![手势识别演示](01_技术报告/演示图片1.0.4/图1%20手势识别演示图.png)
