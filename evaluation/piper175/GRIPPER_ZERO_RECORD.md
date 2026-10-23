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
