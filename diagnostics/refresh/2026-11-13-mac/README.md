# Mac 实机刷新证据（2026-11-13）

使用 PR #1 的原计时循环、当前共享源码、协议 01、160×80，同一小屏，每轮固定与移动各 120 帧，目标 10 FPS。三轮共 720 帧；第三轮应用户要求重播，用户确认“现在很明显流畅了很多”。完整表格与解释见 [跨平台对照](../../../docs/refresh-comparison.md)。

| 文件 | 内容 |
| --- | --- |
| [timing-run-1.json](timing-run-1.json) | 首轮逐帧计时及汇总 |
| [timing-run-2.json](timing-run-2.json) | 第二轮逐帧计时及汇总 |
| [timing-visual-replay.json](timing-visual-replay.json) | 第三轮目视重播逐帧计时及汇总 |
| [host-calls.json](host-calls.json) | 独立补采固定 i=0、移动 i=1 两帧主机调用，不参与计时统计 |
| [summary.json](summary.json) | 三轮汇总、调用核对、用户观察和四份原始文件 SHA-256 |

单帧中位约 44–50 ms，移动阶段帧起始间隔中位约 100 ms。固定阶段长耗时全部保留，未交换测试顺序。独立跟踪每帧 6 次 _write、655 次 serial.write、4 次 serial.read，没有短读短写，像素摘要与 Windows 补采一致。

运行提交 41142a302e1a4a3dc90cbaf509290b2a2b6761f9；源码哈希、平台和依赖在各 JSON metadata 中。正式源码与计时循环未改，无闪存写入。主机跟踪不是 USB 抓包，通信结束不是 LCD 更新结束，目视确认只覆盖直接 10 FPS 演示。

复测时释放串口、在相同源码目录运行，替换实际 Python 和端口：

```sh
.venv-mac/bin/python -B benchmark_refresh.py --check
.venv-mac/bin/python -B benchmark_refresh.py \
  --port /dev/cu.usbmodem实际端口 --output validation/mac-refresh.json
.venv-mac/bin/python -B trace_refresh_calls.py \
  --port /dev/cu.usbmodem实际端口 --output validation/mac-refresh-host-calls.json
```

公开文件只有合成测试图、控制命令、函数名/行号和环境信息，不含序列号、用户名路径、私人素材或凭据。
