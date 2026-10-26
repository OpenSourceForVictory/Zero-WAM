# 双 Piper 夹爪置零记录

本文件用于下次夹爪零点标定。每只夹爪分别记录；标定前先完成“标定前”部分，不能事后用新零点反馈补填旧值。左臂为 USB `1-2.2`，右臂为 USB `1-2.1`；操作时以 USB 物理口确认当次 CAN 接口。

## 操作信息

- 日期、时间：2026-10-23 16:04–16:20（北京时间）
- 操作人及在场确认：待记录
- 软件版本、固件版本、当次 CAN 接口：工控机 `piper_new` 环境 `pyAgxArm` 和 `piper_sdk`；左 USB `1-2.2` → 当次 `can1`，右 USB `1-2.1` → 当次 `can0`；两臂均为固件 `S-V1.7-3`、硬件 `H-V1.2-1`
- 推理、遥操、采集会话状态：网页 `/api/status` 显示推理和控制任务未运行、控制未连接；进程检查无在运行的臂控制脚本
- 标定对象：先左后右；左、右均已发送厂家 SDK 置零帧；两侧 0 mm 实物闭合及再次开合后的复测均由操作人确认通过

## 标定前

| 指标 | 左夹爪 | 右夹爪 |
| --- | --- | --- |
| 空夹、0 mm 指令下实际可见缝隙（测量方法及照片） | 腕部画面可见缝隙；实测毫米数待现场测量；`artifacts/gripper_zero_20261023/gripper_zero_before_left.jpg` | 腕部画面可见缝隙；实测毫米数待现场测量；`artifacts/gripper_zero_20261023/gripper_zero_before_right.jpg` |
| 0 mm 指令后反馈开口、使能和故障位 | 0.50 mm；使能；所查故障位均 False | 0.28 mm；使能；所查故障位均 False |
| 10 mm 指令后反馈与实测开口 | 反馈 9.00 mm；实测待记录 | 反馈 9.59 mm；实测待记录 |
| 30 mm 指令后反馈与实测开口 | 反馈 29.00 mm；实测待记录 | 反馈 29.61 mm；实测待记录 |
| 失能后能否轻松手动合拢、最终物理零位 | 操作人确认能合拢且松手后保持接触；第一次标定前反馈约 −4.9 mm，标定后约 −1.8 mm；厂家 SDK 第二次发送前约 −2.6 mm、之后约 −1.7 mm；接触位置照片 `artifacts/gripper_zero_20261023/left_manual_closed_left.jpg` | 操作人确认能合拢、手已移开；厂家 SDK 发送前约 −4.9 mm、之后约 −1.47 mm；照片 `artifacts/gripper_zero_20261023/right_manual_closed.jpg` |

## 标定及标定后

| 指标 | 左夹爪 | 右夹爪 |
| --- | --- | --- |
| `disable_gripper()` 后失能反馈 | 16:06 左夹爪反馈 `enabled=false`、开口 0.50 mm；接口即时返回 False，但后续状态明确失能（官方说明该返回值可能受即时反馈影响）。第二次厂家 SDK 失能也确认 `enabled=false` | 厂家 SDK 发送失能命令后反馈 `enabled=false`、约 −0.42 mm、无过流 |
| `calibrate_gripper()` 调用时间、返回值 / ACK | 第一次约 16:08:22：`calibrate_gripper(timeout=2.0)` 返回 `False`，未收到成功 ACK。第二次按厂家 `piper_sdk` 示例发送 `GripperCtrl(0,1000,0x00,0xAE)` 一次，该旧接口没有 ACK 返回；失能反馈从 −2.6 mm 变为 −1.7 mm。原始输出 `artifacts/gripper_zero_20261023/left_calibrate_result.txt` 与 `left_sdk_setzero_result.txt` | 厂家 SDK `GripperCtrl(0,1000,0x00,0xAE)` 一次；该旧接口没有 ACK 返回，失能反馈从 −4.9 mm 变为 −1.47 mm；原始输出 `artifacts/gripper_zero_20261023/right_sdk_setzero_result.txt` |
| 标定时指尖位置和照片 | 手动轻合后 `left_manual_closed_left.jpg`、第二次 `left_manual_closed_retry.jpg`；重新下发 0 mm 后 `left_after_sdk_zero_test.jpg` | 手动轻合 `right_manual_closed.jpg`；重新下发 0 mm 后 `right_after_zero_test.jpg` |
| 0 mm 指令后反馈与实测开口 | 第二次厂家 SDK 置零帧后首次 0 mm 反馈 −0.50 mm；完整 10→30→0 mm 复测后反馈 0.50 mm，操作人确认仍完全贴合 | 厂家 SDK 置零帧后首次 0 mm 反馈 −0.42 mm；完整复测后反馈 0.42 mm，操作人确认仍完全贴合 |
| 10 mm 指令后反馈与实测开口 | 最终复测反馈 9.00 mm；实测毫米数未测 | 最终复测反馈 9.45 mm；实测毫米数未测 |
| 30 mm 指令后反馈与实测开口 | 最终复测反馈 28.00 mm；实测毫米数未测 | 最终复测反馈 29.33 mm；实测毫米数未测 |
| 是否出现过流、过热、通信或其他故障 | 检查过程中无过流、过热或驱动故障反馈；`pyAgxArm` 第一次置零未收到成功 ACK，旧 `piper_sdk` 接口本身不提供 ACK 返回 | 检查过程中无过流、过热或驱动故障反馈；旧 `piper_sdk` 接口本身不提供 ACK 返回 |

## 推理影响核查

- 前后同指令反馈差值（0 / 10 / 30 mm）：左 +0.00 / +0.00 / −1.00 mm；右 +0.14 / −0.14 / −0.28 mm。实物 0 mm 从留缝变为接触，但没有尺量的物理缝隙数值；不能由反馈差值推算真实指尖位移
- 原始采集数据与模型权重：保持原样；记录所用权重路径，不覆盖训练统计
- 同权重受控抓取测试的场景、提示词、示教、运行目录及结果：尚未执行
- 是否需要客户端补偿或用新标定数据重新训练：现有数据不支持立即修改客户端或训练统计；先做同权重受控抓取测试

标定只在夹爪能轻松达到正确物理零位且无物体时进行；若机械止点仍留缝，先检查指尖安装与机构，不把有缝位置写成零点。

标定前只读/闭合日志保存在工控机：`/home/piperpika/piper_runtime/recovery_checks/gripper_left_20261023_160453`、`gripper_right_20261023_160458`、`gripper_left_20261023_160539`、`gripper_right_20261023_160543`。后两条分别下发 0 mm 目标一次、没有写入零点或发送关节命令。

验收说明：`pyAgxArm.calibrate_gripper()` 第一次返回 `False`，因此无法从 ACK 证明写零成功；后续按厂家 `piper_sdk` 示例各发送一次 `set_zero=0xAE`，旧接口无 ACK。两侧 0 mm 指尖由操作人确认贴合，10→30→0 mm 再次开合后仍贴合。此次只发送夹爪命令，没有发送关节运动指令。不能仅凭实际改善区分“零点写入”与“手动调整了指尖/机构位置”。


## 2026-10-26 断电后复核与响应协议排查

- 操作人确认机械臂曾断电；本次分别下发 0 mm 后左反馈 0.20 mm、右 0.07 mm，均使能且无故障，但操作人确认仍有缝隙。不能据此断定零点未保存，也不能认定已保存。
- 左夹爪再次失能，操作人手动合拢并确认手已移开。`pyAgxArm.calibrate_gripper(timeout=2.0)` 返回 False；反馈由 -7.50 mm 变为 -1.80 mm，保持失能。未再次写零、未使能开合。原始记录：`artifacts/gripper_recalibrate_20261026/left_calibrate.txt`。
- 已安装 pyAgxArm 的实现发送 `0x159` 夹爪命令、`set_zero=0xAE`，但等待设置响应中的 `instruction_index=0x75` 且 `is_set_zero_successfully=1`。
- 已安装两套 SDK 对响应 `0x476` 的注释均描述为 0x4XX 设置指令应答，成功位仅用于关节置零。这与夹爪接口的等待条件存在值得核查的不一致，但尚不足以确定 SDK 缺陷或固件不支持。
- 旧 piper_sdk 的 GripperCtrl 只发送命令，不等待控制器置零 ACK；“旧接口没有 ACK”应理解为接口不返回标定确认，不能理解为硬件一定不发送响应。
- False 仅说明 SDK 未满足预期成功条件；既可能超时/响应不匹配，也可能返回失败。未保存当次原始 CAN 应答，无法区分。未发现文档要求另行调用保存接口，固件掉电持久化仍待厂家确认与断电复测。
- 下一步优先采集置零时原始 CAN 请求/应答，核对 0x159、0x476 及索引；不应仅延长超时或反复写零来掩盖原因。当前左夹爪保持失能，右侧未修改。

### 10:05 原始 CAN 接收抓包

- 在左夹爪仍失能且反馈保持 -1.80 mm 时进行一次置零调用，同时独立 SocketCAN 接收抓包；SDK 等待 2 秒返回 False，再额外观察 2 秒。
- 收到连续的关节、夹爪等反馈帧；未观察到 0x476 应答。故本轮不是已经抓到 0x476 成功应答而 SDK 未识别；但仍不能由此断言硬件置零失败或未保存。
- 抓包未捕获本机 0x159 发送帧。查明 pyAgxArm 的 CAN 通信默认 local_loopback=False，因此独立监听 socket 不会看到本机发送回环；本次抓包只能证明接收侧未观察到应答，不能作为发送帧的线缆侧送达证明。
- 置零后反馈 -1.50 mm，仍失能、无故障。没有执行开合、关节运动或后续重复置零。
- 本机证据：artifacts/gripper_recalibrate_20261026/{capture_result.txt,can_frames.json,capture_zero.py}；工控机证据：/home/piperpika/piper_runtime/recovery_checks/left_zero_can_20261026_100552/。

### 两侧重新闭合与开合复测

- 左侧重新使能下发 0 mm 后反馈 -0.40 mm，操作人确认完全贴合。随后 10→30→0 mm 反馈分别 9.30、29.30、0.00 mm，均使能、无故障。
- 右侧先失能，操作人手动合拢并确认手已移开；置零前 -4.69 mm，置零后 -1.40 mm。此次对临时测试连接启用 local_loopback=True，抓到一帧 0x159 / 00000000000000ae，未收到 0x476，SDK 仍返回 False。未修改生产 SDK 配置。
- 右侧重新使能下发 0 mm 后反馈 -0.49 mm，操作人确认完全贴合。随后 10→30→0 mm 反馈分别 9.38、29.47、0.35 mm，均使能、无故障。
- 两侧目前均使能，最后目标 0 mm；未发送关节运动指令。开合后的最终照片已保存，操作人确认的是开合复测前的闭合状态；断电保存尚未验收，不能记作永久标定完成。
- 本机原始记录与照片：artifacts/gripper_recalibrate_20261026/；右侧 CAN 记录：/home/piperpika/piper_runtime/recovery_checks/right_zero_can_20261026_101044/。

### 10:21 右夹爪再次置零

操作人确认合拢并移开手后，再发送一次 0x159 / 00000000000000ae；无 0x476 成功应答。反馈从 -1.40 mm 变为 -1.26 mm；使能下发 0 mm 后为 -0.35 mm，操作人确认贴合。本轮没有再做开合或断电复测，不能认定持久化成功。证据：artifacts/gripper_recalibrate_20261026/right_repeat_102136/。
