# Windows 刷新实测与 Mac 对照（2026-11-13）

Mac 已按相同计时循环完成三轮 720 帧实机测试及两帧调用跟踪。见 [Mac 原始证据](2026-11-13-mac/README.md) 和 [跨平台对照](../../docs/refresh-comparison.md)。单帧中位约 44–50 ms，固定阶段的偶发长耗时完整保留。以下 Windows 原统计及补采范围不变。

本说明整理已完成的 Windows 实机测试。固定图和移动方块各 120 帧，目标 10 FPS，生成到通信检查完成的中位数分别为 **46.167 ms / 46.137 ms**。这些数字测的是主机通信路径，**通信完成不等于 LCD 刷新完成**。主机函数跟踪不等于 USB 总线抓包；没有据此证明双缓冲、整屏原子换帧或固件扫描方式。

本次 PR 只增加诊断脚本、测试和报告。没有修改正式协议、分包、刷新策略或固件，也没有写入闪存。现有 Windows 工程和未完成的合并副本均保留，PR 从最新 main 的独立克隆建立。

## 文件、证据与版本

- [benchmark_refresh.py](../../benchmark_refresh.py)：从原 PowerShell 内嵌 Python 脚本整理的可移植入口。画面、120 帧、10 FPS、计时边界、排序下标及核心计时循环保持一致。新增端口参数、显式源码目录、环境识别、无硬件检查和 JSON 导出；原先没有 JSON 导出。
- [trace_refresh_calls.py](../../trace_refresh_calls.py)：独立补采脚本，记录显示调用内的实际请求/返回长度、调用栈、命令字节及像素载荷摘要，不增加设备 I/O。
- [original-summary.txt](2026-11-13-windows/original-summary.txt)、[JSON](2026-11-13-windows/original-summary.json)、[CSV](2026-11-13-windows/original-summary.csv)：原实机运行已保存的汇总输出，已去掉端口标识及无关启动输出。原始每帧数组、原 240 帧的逐次调用长度和载荷摘要**未持久保存**，不能由分位数反推出，也没有补造 CSV 原始帧。
- [supplemental-host-calls.json](2026-11-13-windows/supplemental-host-calls.json)：后来使用**相同 Windows 源码哈希**补采的两帧实机主机调用跟踪。固定图用 i=0，移动图用 i=1；它不是原 240 帧中的记录，不用于重算原耗时。
- [main-source-host-calls.json](2026-11-13-windows/main-source-host-calls.json)：后来在**同一 Windows 主机、同一设备**运行仓库 main 源码所得的两帧跟踪。用于检查脚本兼容性及 6 写/4 读路径；**不是 Mac 实机结果**。
- [windows-source-excerpts.md](2026-11-13-windows/windows-source-excerpts.md)：被测 Windows 文件的相关函数原文、行号和哈希，作为未提交源码差异的证据；没有替换仓库正式文件。

被测 Windows 源码尚未置于 Git 仓库，没有可提供的提交号。原运行实际入口是 Windows 工程目录内的 PowerShell stdin → 该项目既有虚拟环境 Python，执行 msu2.Display.connect() 后直接调用 Display.show()，**不经过 GUI 的 app.py**。桌面 GUI 的启动链是 Start.cmd → pythonw.exe → app.py；它不是这次 240 帧计时的入口。

| 文件 | 被测 Windows SHA-256 |
|---|---|
| msu2.py | 2990752e4a8a0e38d81b66d4b0d3604353b92e27604032f9046742fbb76e10e7 |
| app.py | de14631e52ed8a8f505c558bf937b174ce8639fb3f65e53f7fb0451c5c228f56 |

哈希在整理 PR 时从实测后未改动的 Windows 文件计算，原计时脚本没有打印哈希；补采又记录了相同哈希。原脚本 Python 正文的 SHA-256 为 1c2ea3887002cbe410973d40bf07aab484055656b5567da4b9a0e31613b95aa2，这是整理前正文的摘要，含原端口赋值，**不是**整理后脚本的摘要。

对照的 main 提交为 [d354cba6d5509414ca66a773a053f482c73c16e0](https://github.com/Cass-ette/msu2-controller/commit/d354cba6d5509414ca66a773a053f482c73c16e0)。独立克隆中的实际文件 SHA-256：msu2.py = f284742c412332161eae439263cc25d399a44ca8b53cf9f4d0990031eb6de261，app.py = 266f42a54549bc50d3b362f8a16d256dcc8a4948bca7eb4d6467760a87fd7a41。这是文件字节摘要，包含本机检出时的换行形式。

环境：Python **3.11.9 / 64-bit**，pyserial **3.5**，Pillow **12.3.0**，psutil **7.2.2**；平台 API 返回 Windows-10-10.0.26200-SP0，不据此额外判断系统商品名称。版本在整理时从原有虚拟环境核实，原测试输出未单独记录版本。设备协议 01、160 × 80；VID/PID 1A86:FE0C 来自连接设备枚举。串口参数 19200、read timeout 0.1 s、write timeout 3 s；波特率配置不能当作 USB CDC 实际吞吐量。

## 原画面与计时口径

两组均使用 160 × 80 RGB 黑底、16 × 16 白块，矩形坐标 (x,24,x+15,39)。固定图 x=20，移动图 x=(i*3)%144，i=0..119。每组独立累计目标时间，发送前 target += 0.1，只在还有余量时 sleep；超时不丢帧、不主动重置目标。每组首帧也先等待约 100 ms。

每帧从生成图像前开始计时，到未改动的 Display.show() 返回后结束。生成包括建图及画块；RGB565 和命令编码通过临时包装原函数独立计时。write_calls 是 **Display._write() 的累计耗时**，含它内部的 serial.write、短写循环和平台等待，不能改称纯 USB 传输耗时。all_serial_reads 是 **serial.read() 的累计耗时**，包含阻塞及读取，不能进一步拆成纯等待/复制耗时。总耗时还含其他 Python/协议处理，不应简单相加各项中位数。

pre_register_reads 在像素前保活阶段统计；display_ack_reads 在命令 7 发出后统计；post_register_reads 在大像素载荷 _write 开始后统计。这个阶段标签是原脚本的标记方式，没有额外发出寄存器、ADC 或 flush。原脚本没有单独测量生成后排队或设备内部处理时间。

每组有 120 个耗时样本、119 个相邻帧起始间隔。中位数用 statistics.median；P95/P99 用排序数组 v[int((n-1)*p)]，无插值。120 帧时分别取从零计的 113、117 下标，119 个间隔时取 112、116。所有数字单位 ms，沿用原输出的三位小数。目标 sleep、连接/描述表读取、打印汇总和关闭串口均不计入单帧总耗时；原来没有补采跟踪的 JSON/摘要开销。

## 原实机统计

| 指标（ms） | 固定图 中位数 / P95 / P99 / 最大 | 移动方块 中位数 / P95 / P99 / 最大 |
|---|---:|---:|
| 生成画面 | 0.081 / 0.156 / 0.163 / 0.246 | 0.077 / 0.160 / 0.167 / 0.217 |
| RGB565 转换 | 1.970 / 2.452 / 2.707 / 3.995 | 1.949 / 2.378 / 2.516 / 2.878 |
| 命令编码 | 1.086 / 1.209 / 1.294 / 1.447 | 1.084 / 1.162 / 1.361 / 2.016 |
| Display._write 耗时合计 | 42.422 / 42.871 / 43.008 / 43.225 | 42.450 / 42.856 / 43.007 / 43.072 |
| serial.read 耗时合计 | 0.411 / 0.527 / 0.620 / 0.659 | 0.415 / 0.535 / 0.575 / 0.593 |
| 发送前寄存器读取 | 0.116 / 0.176 / 0.231 / 0.327 | 0.112 / 0.178 / 0.236 / 0.268 |
| 显示应答读取 | 0.171 / 0.232 / 0.261 / 0.390 | 0.172 / 0.249 / 0.285 / 0.295 |
| 发送后寄存器读取 | 0.118 / 0.172 / 0.207 / 0.254 | 0.118 / 0.177 / 0.189 / 0.227 |
| 生成到 show 返回 | 46.167 / 46.966 / 47.140 / 48.697 | 46.137 / 46.787 / 46.996 / 47.377 |
| 帧起始间隔 | 100.008 / 100.328 / 100.378 / 100.485 | 99.997 / 100.453 / 100.495 / 100.685 |

240 帧均完成通信检查；两组每帧 _write 调用数集合均为 [8]，serial.read 调用数集合均为 [6]。这是**原实测记录**。原 240 帧没有记录每次 serial.write 的返回值，不能宣称已经在这些旧帧中逐次排除了短写。两组相近的传输耗时与整帧发送相符；相同固定图也没有跳帧或变化区域优化。

## 8 次写入、6 次读取的实际顺序

下表来自后补采的 Windows 两帧，二者控制路径和返回长度相同。W/R 是实际 serial.write/serial.read 顺序编号，表内写入同时对应一条 _write()；**_write() 自身返回 None**，实际接受的字节数由下层 serial.write 记录。补采时没有短读、短写或超时。**不能把这个后补采结论扩展为原 240 帧的逐次证据。**

| 顺序 | 来源函数（被测 Windows msu2.py 行号） | 调用 | 请求 → 实际返回长度 | 请求或应答 |
|---|---|---|---:|---|
| 1 | show:245 → keep_alive:186 → read_u16:195 → _exchange:153 → _write:145 | W1 | 6 → 6 | 00 30 20 00 00 00 |
| 2 | 同上 → _exchange:154 → _read_exact:134 | R1 | 6 → 6 | 00 30 20 00 00 a0 |
| 3 | show:245 → keep_alive:186 → read_u16:195 → _exchange:153 → _write:145 | W2 | 6 → 6 | 00 30 20 01 00 00 |
| 4 | 同上 → _exchange:154 → _read_exact:134 | R2 | 6 → 6 | 00 30 20 01 00 50 |
| 5 | show:246 → _write:145 | W3 | 6 → 6 | 02 00 00 00 00 00 |
| 6 | show:247 → _write:145 | W4 | 6 → 6 | 02 01 00 a0 00 50 |
| 7 | show:248 → _write:145 | W5 | 6 → 6 | 02 03 07 00 00 00 |
| 8 | show:249 → _read_exact:134 | R3 | 2 → 2 | 02 03 |
| 9 | show:250 → serial.read | R4 | 4 → 4 | 07 00 00 00 |
| 10 | show:253 → _write:145 | W6 | 39000 → 39000 | 像素命令载荷；仅保存摘要，见下 |
| 11 | show:255 → read_u16:195 → _exchange:153 → _write:145 | W7 | 6 → 6 | 00 30 20 00 00 00 |
| 12 | 同上 → _exchange:154 → _read_exact:134 | R5 | 6 → 6 | 00 30 20 00 00 a0 |
| 13 | show:255 → read_u16:195 → _exchange:153 → _write:145 | W8 | 6 → 6 | 00 30 20 01 00 00 |
| 14 | 同上 → _exchange:154 → _read_exact:134 | R6 | 6 → 6 | 00 30 20 01 00 50 |

补采固定图 i=0 的 39,000 字节像素命令载荷 SHA-256 为 f522dc3d0410040cc1184c83fac19e44cef65f14c6321feaca8e065ccb1ec02e；移动图 i=1 为 efb017e39160842cb15b9fb81e61d85253a365edb08b5f7feb6f8114b0e55d41。这些是**编码后载荷**摘要，不是截图或原始 RGB565 文件。

W1/W2 是 show 内额外的发送前尺寸检查；W7/W8 是像素发送后的尺寸检查。测试脚本自身没有追加寄存器请求。没有 ADC 轮询；没有 serial.flush/drain、独立刷新命令或测试脚本添加的设备操作。连接时的 HELLO/描述表读取在计时/跟踪范围之外；两帧补采之间的 100 ms 主机 sleep 也不在表内。

## 为什么 main/Mac 模型为 6 写、4 读

这是确认的**源码差异**，不只按次数猜测：

- 原 Windows [show:239、keep_alive:183 的摘录](2026-11-13-windows/windows-source-excerpts.md)在配置显示前强制读取宽、高。仓库 [main show:305](https://github.com/Cass-ette/msu2-controller/blob/d354cba6d5509414ca66a773a053f482c73c16e0/msu2.py#L305)没有这一调用。删去表中 W1/R1、W2/R2，剩下恰好 6 写/4 读。本 PR **没有进行这样的删除**。
- 原脚本的 writes 数的是 _write()，而 reads 数的是 serial.read，层级不一致。在两帧 Windows 补采中，8 条 _write 实际对应 8 次 serial.write；在仓库 main 补采中是 6 条 _write 对应 6 次 serial.write、4 次 serial.read。后者仍在 Windows 主机上运行，证明 8/6 与 6/4 的差别不能简单归为操作系统分包。
- main 的 [_write:151](https://github.com/Cass-ette/msu2-controller/blob/d354cba6d5509414ca66a773a053f482c73c16e0/msu2.py#L151)只在 Darwin 对大载荷使用最多 60 字节写入；show 设置 _display_payload=True，使实时像素分包省去旧的 1 ms 等待。39,000 / 60 = **650** 次 serial.write；加上剩余 5 条短协议写入，完整应答、无短写时整帧为 **655 次底层 serial.write**，但仍为 **6 次 _write**。这一源码计算现已由另行补充的 [Mac 两帧实机跟踪](2026-11-13-mac/host-calls.json)验证；不属于原 Windows 240 帧记录。
- _read_exact() 可能多次读到部分数据；Windows _write() 也会继续发送短写余量，所以底层调用次数不等于协议命令数。main 的 [_display_ack:186](https://github.com/Cass-ette/msu2-controller/blob/d354cba6d5509414ca66a773a053f482c73c16e0/msu2.py#L186)比旧 Windows 多了启动标识识别及应答后缀分段读取，异常或短读时调用数可增加。补采记录包含请求长度和实际返回长度，诊断测试还以假串口验证了这一统计区别；假串口测试不是硬件证据。
- GUI 在显示调用之外还能进行独立保活、ADC 和通知调度；本次直接调用 show 排除了这些操作。抓整个进程或 USB 总线时应先统一范围，不能拿整个 GUI 任务的计数与这里单帧计数直接比较。

## 画面生成、编码与设备刷新边界

Windows 调用链为：原计时脚本生成 PIL 图 → show:239 → 发送前尺寸检查 → 三条显示配置写入 → 显示应答读取 → image_bytes:115 → encode_pixels:82 → _write:139 → 两个尺寸寄存器检查。image_bytes 生成 RGB565 大端的 25,600 字节；编码为 100 组，每组 64 条六字节暂存数据命令（04 slot data[4]）及一条 02 03 08 01 00 00，合计 39,000 字节。两个版本相同画面得到相同长度/摘要；每次发送覆盖完整 160 × 80，方块移动时也不是局部区域更新。

实际已见命令为 02 00、02 01、02 03 07、像素暂存 04 和块命令 02 03 08，随后 00 30 20 寄存器检查。没有另发 02 03 09 或其他整帧结束/缓冲切换指令。代码对块命令的称呼与命令字节一致性不提供固件内部刷新时刻的证据；寄存器检查也不证明整屏已扫描完成。

GUI 的调用链和计时脚本不同。旧 Windows app.py 的 frame_request:450 → render_request:465 → request_frame:484 → show，由 tick:525 调度；run:322 使用单工作线程，busy 时不积压每个目标帧，generation 改变会丢弃已过时的生成结果。动态帧缓存用于当前模式预览和保存当前帧，不是与设备上帧比较的差分缓存；没有相同帧跳过或多个像素帧合并发送。tick 还优先安排每秒保活、约 30 ms 的按键轮询。main 的对应入口为 app.py frame_request:512、render_request:522、request_frame:541、tick:584；保活按上次通信时间判断，而不是每次 show 必做发送前检查。

因此要区分四件事：数据采样、主机生成、串口发送、LCD 更新。原脚本没有真实传感器采样；每组生成/发送目标都是 10 FPS。GUI 的电脑状态、功率、时钟通常按秒生成，投屏目标可选 2/5/10 FPS，还受截图、任务占用和调度影响。Mac 的 60 字节像素调用与 Windows 的一次大写入还会改变主机供给数据的节奏，即使通信总耗时接近，肉眼观感仍可能不同。未获得固件或 USB/光学证据前，这些只是需要对照的环节，不能断言它们已经解释了撕裂、逐块更新或整屏换帧。

## 兼容性与复测

benchmark_refresh.py --check 已分别在当前 main 源码和被测 Windows 源码上通过。两份补采已实际连接同一小屏完成显示，提供额外的兼容性证据。main **没有** keep_alive API 不影响此脚本，因为脚本不自行调用它；只调用被选中的 Display.connect/show/close、image_bytes、encode_pixels。脚本没有把 Windows show 或分包规则带进 main，也没有调用仓库 benchmark_transport.py 中替换传输参数的实验路径。

Mac 从 PR 分支根目录运行同一脚本：

~~~sh
.venv-mac/bin/python -B benchmark_refresh.py --check
.venv-mac/bin/python -B benchmark_refresh.py \
  --port /dev/cu.usbmodem实际端口 \
  --output validation/mac-refresh.json
.venv-mac/bin/python -B trace_refresh_calls.py \
  --port /dev/cu.usbmodem实际端口 \
  --output validation/mac-refresh-host-calls.json
~~~

请将示例端口替换为实际端口。诊断脚本本身没有写死端口。--source-dir /path/to/source 可明确选择本机实际运行的源码树；输出带该树的提交号（若存在）及源码哈希，避免把 main 的结果误认为旧 Windows 结果。应核对该 JSON 的哈希，而非仅凭脚本所在目录判断版本。要重现原 Windows 8/6 路径需要本文哈希对应的 Windows 文件；仓库 main 并未包含这份本地改动，报告的源码摘录不等于完整可执行源码分发。

比较时使用同一设备、固件、线材和同一固定图/方块/帧数/10 FPS，先关闭 GUI 和其他串口消费者。先运行原计时循环，另跑调用跟踪（跟踪有堆栈/摘要开销，不能混入耗时比较）。记录各端 JSON 的源码哈希、系统与依赖版本、实际调用层级、分位数和帧间隔。随后再单独比较 GUI：相同投屏或内容生成模式、相同采样配置，并分别计时采样、排队、生成和发送。

如需解释肉眼差别，再做相同条件的 USB 总线抓包和同步高帧率拍摄/光学测量，观察帧内各区域改变的时刻。当前证据只覆盖通信与主机调用；没有设备刷新完成时间或内部缓冲结构的证据。

本地诊断回归：python -B -m unittest -v test_refresh_benchmark。它检查分位数算法、短读/短写分层计数及摘要记录，不连接设备。公开目录只含合成测试画面的长度/摘要和控制命令，不含用户名路径、凭据、设备备份、私人截图或聊天记录。

## 本 PR 验证记录

- 6 项诊断/传输回归通过（test_refresh_benchmark、test_transport），均为模拟测试。
- 两种源码的无设备导入/哈希检查通过；原 Windows 与当前 main 各补采两帧实机显示，数据分别保存。
- 原 120 帧循环、四个计时包装与分位数函数的 AST 与原脚本一致；只整理外围 CLI、环境及导出。
- 通过假串口执行完整 CLI 并导出 240 帧 JSON，验证恢复原函数；该结果不作为硬件数据发布。
- Windows app.py/msu2.py 哈希未变；生产源码相对 main 无改动。
