# MSU2 Controller

带图形界面的 MSU2 MINI 小屏控制器。Windows 与 macOS 共用 Python/Tk 核心，支持文字、时钟、电脑状态、图片、投屏、离线动画及通用通知。

[下载发行版](https://github.com/Cass-ette/msu2-controller/releases) · [图片取景](图片取景.md) · [通知 SDK](通知接口.md) · [功率与电池](功率与电池.md)

## 安装

**Mac（Apple 芯片）**：下载 Release 中的 `.dmg`，将「MSU2 小屏控制器」拖到 Applications 后打开。内置 Python、Tk 和依赖，无需终端或额外安装。当前为 ad-hoc 本地签名，尚未做 Developer ID 签名与 Apple 公证。最低版本标记 macOS 13，实机验证环境为 macOS 27.2；Intel 版本尚未提供。

**Windows**：当前提供共享源码；Windows 最新工作区将在独立分支合并并验证后发布安装包。不能将 Mac 上的模拟 Windows 测试视作 Windows 实机验证。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

安装完成后双击 `Start.cmd`。需要含 Tkinter 的 Python 3.11 或更新版本。

Mac 开发者可使用源码模式：

```sh
bash Setup.command
bash Start.command
```

## 功能

- 中文文字、时钟、自定义时钟底图、电脑状态、图片及主屏投屏。
- 图片、时钟底图及 RGB 素材支持拖动取景、缩放、左右 90° 旋转、完整显示和铺满，保留原图。
- Mac 功率与电池：整机、实际输入、电池充放电功率，能量流图和五分钟趋势；缺失值、估算及过期数据明确标注。Windows 当前提供电量、接电与剩余时间，功率遥测待适配。
- 实体按键切换模式，正向 APP 预览；设备安装方向的 180° 倒转仅作用于输出、BIN 导出和烧录。
- GIF / 序列帧转 36 帧离线动画，支持预览、裁剪、重采样及动画备份导入。
- 时钟背景和相册槽读取、备份、上传、恢复、原图倒转写回；图片 / RGB565 BIN 互转。
- 读取已有 ASCII 字库及单色图，由主机解码并叠加背景；不同素材的地址和编码需按实际设备核对。
- 任意应用可用标准库 Python SDK / 本机 HTTP 提交通用通知；队列、过期、临时显示及原画面恢复由控制器管理，核心没有特定聊天或开发工具的业务逻辑。

未完成：自定义单色上传、字库生成烧录、整片闪存备份恢复、扩展外设和主控固件升级。

## 连接与设备数据

先退出其他串口助手，连接数据线后刷新并连接设备。当前实测为 160×80、协议 01 的 MSU2 MINI，USB VID:PID 为 `1A86:FE0C`。Mac 原生 CDC ACM 端口为 `/dev/cu.usbmodem…`，先检查系统原生支持；不能仅凭 VID 安装 CH340 驱动。

识别失败按「USB 是否出现 → 是否生成串口 → 是否握手成功」排查：

```sh
bash Diagnose.command
```

投屏需要为实际运行的控制器应用、Python 或终端授权屏幕录制。Mac 当前支持主屏，Windows 源码支持主屏 / 整个桌面选项。通知、实时显示、按键和闪存共用唯一串口任务线程，避免写入期间插入其他请求。

素材和动画写入均先备份到磁盘、再写入、完整回读校验。开始擦写后请保持连接；不要把抽样验证与完整备份 / 回读的耗时混为一谈。动画区域备份不等于整片闪存备份。

Mac 安装版将数据存放在应用外部，更新 `.app` 保留数据：

| 内容 | 位置 |
| --- | --- |
| 自动备份 | `~/Library/Application Support/MSU2Controller/backups/` |
| 通知发现文件 | `~/Library/Application Support/MSU2Controller/.runtime/api.json` |
| 日志 | `~/Library/Logs/MSU2Controller/` |

源码版继续在项目目录保存 `backups/`、`.runtime/` 和 `logs/`。备份与通知凭据不应提交到 Git。

## 开发和验证

```sh
python -B -m unittest discover -v
```

测试包含真实 Tk 事件循环及模拟设备，需可用的图形会话；Linux CI 使用 Xvfb。最新 Mac 完整回归 138 项通过；另完成 600 帧混合内容传图、打包运行时及真实串口检查。结果和限制见 [验证记录](docs/testing.md)。

Mac 实时传图保留 60 字节分包，移除原每包 1 ms 人为等待；混合内容单帧含编码 / 传输 / 应答约 50 ms，闪存长写仍保留保守节奏。未实现固件双缓冲，刷新差异仍需与 Windows 实际版本对照。

构建 Mac 应用和 DMG：

```sh
python3 -m venv .venv-build-mac
.venv-build-mac/bin/python -m pip install -r requirements.txt -r requirements-build-mac.txt
.venv-build-mac/bin/python -B build_mac_app.py
```

构建输出在 `dist/`。仅 PyInstaller 是新增构建依赖；运行依赖仍是 pyserial、Pillow 和 psutil。使用 `MSU2Controller.spec` 打包共享 `app.py`，系统差异集中在串口、字体、权限、电源采集、启动与数据路径。

两端协作请遵循 [贡献与 Windows 合并流程](CONTRIBUTING.md)：一个仓库维护共同核心，各系统发布自己的安装产物，通过分支和 PR 合并功能。

## 厂商原始资料

墨砺工作室的原版 [使用及开发指南 PDF](third_party/vendor-msu2/使用及开发指南-MSU2系列可编程USB屏幕_20241102224357.pdf) 归档在 [third_party/vendor-msu2](third_party/vendor-msu2/README.md)，保留原文件与 SHA-256，便于核对硬件、原版工具及接口说明。该文档来自厂商，不是本控制器的使用说明。

## 许可

本项目采用 [MIT License](LICENSE)。Python、Tcl/Tk、Pillow、pyserial、psutil 和 PyInstaller 的相关许可见 [assets/licenses](assets/licenses)，分别适用于各自组件。

`third_party/vendor-msu2/` 内的厂商原件保留原权利人的版权，不适用本项目的 MIT 许可；归档说明见该目录。
