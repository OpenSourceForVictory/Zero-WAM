# 175 Piper 推理运行手册与操作记录

目标：在 175 上完成实时三相机输入、模型推理、双臂与夹爪执行闭环。174 为参考客户端。
部署目录：`/home/piperpika/piper_runtime/inference_client`。
本地源码：`evaluation/piper175/inference_client`。脚本不保存 SSH/sudo 密码。

## 重启后启动

在 175 普通用户终端中执行（不要用 sudo 运行整个客户端）：

```bash
cd ~/piper_runtime/inference_client
bash operate.sh prepare
```

prepare 按 USB 物理位置启动 CAN（必要时 sudo 提示密码）、启动用户相机服务，
然后只读检查硬件；不使能、不回零、不清除停止标志。
左臂 USB 1-2.2:1.0，右臂 USB 1-2.1:1.0；枚举名不可作为永久左右标识。
相机网页： http://192.168.8.175:8080 。
顶部/左腕/右腕序列号依次为 CC1C8100041/CC1C810002Y/CC1C8100015。
当前旋转均为 0°。推理从 /observation.npz 取实时无损原帧，不使用网页截图。

当前模型通路：175:39056 → 工作站:49056 → 1.95.69.29:29056。
工作站原有 49056 模型转发必须先可用；它不是 175 本地模型。
如需重建工作站至模型服务器的第一段（密钥路径仅适用于当前工作站）：

```bash
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 -i /home/huawei/dgh/29.kem \
  -L 127.0.0.1:49056:127.0.0.1:29056 root@1.95.69.29
```

第二段在工作站另一个终端运行并保持：

```bash
bash evaluation/piper175/inference_client/relay_from_workstation.sh
```

已有可用隧道时无需重复运行；端口占用时脚本会失败，不会杀掉现有连接。
旧 tunnel.sh 是 175→174 的另一种路径，仅当 174:39056 有可用服务时使用。
不要让两台客户端同时重置/占用同一个有状态模型会话。

## 检查、推理与运动

在 175 上：

```bash
cd ~/piper_runtime/inference_client
bash operate.sh check                     # 硬件+服务连通，无运动、无模型 reset
bash operate.sh shadow bread_test000      # 最新画面推理一次，重置模型会话，不运动
bash operate.sh once bread_test000        # 实际运动：只执行一个动作块，供首次验收
bash operate.sh execute bread_test000     # 实际运动：持续推理执行，与174启动循环一致
```

运动入口必须在现场具备执行条件时使用。默认6%速度、legacy174夹爪逻辑、
不启用额外15°角度差限制；会发送模型绝对关节目标，可能与当前姿态相差较大。
保留示教状态、故障、发送前新鲜反馈检查和固件夹爪范围限制。到位等待恢复174原版：
状态到位或角度达标，超时/停滞返回False，调用处继续运行。
可显式覆盖 --speed-percent 1、--gripper-mode hold、--max-target-delta-deg 15。
只运行 run.sh 默认 check；run_human_demo.sh 默认 shadow，实际运动需 --execute。

```bash
bash operate.sh stop      # 请求软件停止；现场急停不能依赖网络或此脚本
bash operate.sh status
bash operate.sh resume    # 现场具备恢复条件后显式清除停止标志，不自行启动运动
```

正常用户停止（软件stop、Ctrl+C、SIGTERM）仅断开连接，不发电子停止/reset/disable。
软件执行异常立即退出动作循环并原子置位软件停止标志，仅断开连接；不自动发送电子停止、reset、disable、保持目标或恢复指令。
正常退出不回零、不失能。不要将关节回零与写入零点校准混为一谈。

## 日志与验收

每次运行在 inference_logs/时间戳_模式/ 保存 manifest.json、result.json，
以及观测和预测（按模式产生）。initial_observation.npz 是启动检查时的快照；
首轮推理会在模型 reset 后重新取图，因此该快照不是精确的线上请求副本。
检查 result.json 的 completed、actuation_started；task_success 未验证时为 null。
shadow 成功仅证明取图与模型返回，不能作为抓取成功或执行闭环成功的证据。

## 2026-10-13 操作记录

- 保留174原始客户端于source174；原入口SHA256：
  0b5df29cea1c1ccda08a3b3c40b1c252ab70420b9d80c0f914c0aed1af531bbc。
- 此前新增15°限制、1%速度、单块执行造成行为差异；最新画面那次首目标
  偏差67.53°，被发送前拦截，未执行机械臂动作。
- 本次恢复6%、持续循环、legacy174夹爪，15°限制改为可选默认关闭。
  本机右夹爪额外受固件0.07m上限限制；左仍保留原0.08m软件上限。
- 修改前远端文件备份：backups/align174_20261013/。
- 发现175刚重启：两路CAN DOWN，反向隧道断开；已恢复1Mbps CAN和隧道。
- 只读检查通过：inference_logs/20261013_173912_953485_check/。
  重启后关节姿态已改变，不能继续假定双臂在零位。
- 离线测试20项通过（禁用无关ROS pytest插件自动加载）。
- 174成功那次的模型权重、示教ID、起始姿态尚未核实。
  当前breakfast/bread_test000来自174脚本示例，不宣称复现成功那次全部条件。
- 本次实机运动验收仍待现场清场确认；不得记录为完整任务已跑通。

## 验证命令（源码工作站）

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_piper175_inference.py tests/test_piper175_runtime.py
bash -n evaluation/piper175/inference_client/operate.sh
bash -n evaluation/piper175/inference_client/relay_from_workstation.sh
```

## 先归零，再推理试跑

现场确认清场且有人可急停后，在175执行：

```bash
cd ~/piper_runtime/inference_client
bash zero_then_once.sh bread_test000
```

脚本先做硬件/模型连接检查，随后左臂1%回零、右臂1%回零，
两臂均到位才重新取图推理，以6%速度执行一个动作块。
回零失败会中止后续流程；推理执行中的到位超时按174原版返回False并继续。回零不动夹爪，不写零点标定，不自动清停止标志。
回零日志位于 ../zero_checks/时间戳_zero_then_once_进程号/。

本次修复后的shadow验证通过：inference_logs/20261013_173944_247349_shadow/，
返回(28,2,16)，未使能、未运动。此结果发生在重新归零前，归零后不得重放。

### 17:42 经用户确认开始的实机试跑

- 执行 zero_then_once.sh bread_test000；回零目录：
  ../zero_checks/20261013_174240_zero_then_once_2911/。
- 左、右分别1%回零成功，final_deg均为六个0；未写标定、未发送回零夹爪指令。
- 推理目录：inference_logs/20261013_174315_817740_execute/。
  归零后重新取图，模型40.57秒返回(28,2,16)，平滑33步。
- 实际发送了关节/夹爪命令，日志打印前三步；随后关键帧3秒到位等待超时，
  程序发送电子停止并退出。此次不是15°限制，不是完整执行成功。
- result.json: completed=false、actuation_started=true、task_success=null。
  completed_loop_steps=0是异常退出时外层计数未更新，不能据此判断未发动作；
  应结合actuation_started与逐步日志判断。
- 停止后只读反馈：两臂ctrl_mode=STANDBY、arm_status=NORMAL，
  motion_status=REACH_TARGET_POS_FAILED，无关节限位/通信错误，电机使能均true。
  停止后姿态不再是零位。电子停止不代表失能或位置保持。
- 未自动重试运动。下一步需记录运动过程的目标/反馈误差、核查3秒等待与实际
  运动耗时及模式切换；不能仅忽略到位失败便宣称跑通。

### 按用户要求恢复174到位等待行为

删除site_controller.py自定义wait_both_motion_done覆盖，直接继承原版控制器。
仍使用原版调用参数timeout=3、tol_rad=3°；MOVE_J时状态到位或角度达标即通过。
超时/停滞打印原版诊断并返回False，原版推理循环不因False中止或电子停止。
保留之前的失败记录作为历史，不将修改后的继续执行等同于实测到位。
停止标志在模型等待和每步发送前检查；继承的到位等待期间不检查此标志，
最多等待调用的3秒超时。此次仅修改、测试和部署，未自动重启运动。

### 17:47 原版到位逻辑实机验证

用户授权直接执行，不再次归零。运行operate.sh once bread_test000。
日志：inference_logs/20261013_174735_086451_execute/。
模型40.26秒返回，发送16步指令，执行循环2.41秒，KV更新15.27秒，进程正常退出。
原版等待多次报告0.5秒关节无变化；最后左J2误差59.98°、右J3误差65.50°。
因此仅客户端流程完成，机械臂未证实到达预测目标，不能记录为任务成功。
未自动开启连续运行或清除控制器电子停止状态。
恢复原版等待后的离线测试22项通过。

### 电子停止后的显式控制恢复

用户授权恢复后已执行并验证：双臂从STANDBY变为CAN_CTRL，MOVE_J，
六电机均使能，arm_status=NORMAL，无关节限位或通信错误。
恢复前后读取的关节角一致；没有发送关节目标、夹爪指令或零点标定。
motion_status仍为REACH_TARGET_POS_FAILED（未发新目标），不能将控制恢复当作运动到位验证。
日志：../recovery_checks/20261013_recover_01/result.json。
脚本：../recover_control.py；调用方式（输出目录必须不存在）：

```bash
cd ~/piper_runtime
bash run.sh recover_control.py --execute --output recovery_checks/新的记录目录
```

内部依次执行SDK reset、enable、1%速度设置、MOVE_J，并验证CAN_CTRL反馈。
此为显式恢复操作，不自动嵌入推理循环；不会自动清软件停止标志或启动推理。

### 17:56 恢复控制后再次回零、推理执行

用户授权回零并执行推理；运行zero_then_once.sh bread_test000。
双臂依次1%回零成功，12个关节均为0；随后实时取图推理。
执行日志：inference_logs/20261013_175652_938400_execute/。
模型40.03秒返回，平滑33步，跳过初始latent后发送17步，KV更新13.72秒，正常退出。
执行中原版等待仍曾报告卡住，目标误差逐渐减小；未更改其处理逻辑。
结束后实读双臂CAN_CTRL/NORMAL/MOVE_J/REACH_TARGET_POS_SUCCESSFULLY，全部使能，
无关节限位/通信错误。实际关节角与末目标一致（度）：
左[-22.183,46.669,-45.420,-14.162,6.880,4.092]；
右[1.203,50.106,-61.818,10.094,16.689,-0.688]。
末状态另存final_left_status.json、final_right_status.json。
本次验证了单动作块的取图→推理→发送→末目标到位→KV更新；
不代表中间每帧均到位，也不代表完整breakfast任务或连续多块执行成功。
正常结束未回零、未失能，保持末姿态。

### 用户要求继续执行：连续推理启动

从上一轮末姿态启动operate.sh execute bread_test000，默认max_chunks=0（连续），
速度6%，不再次回零。上一轮客户端已退出，因此新进程会reset模型会话、重新取图，
不是复用上一进程的KV会话。控制台记录：session_logs/20261013_continuous.log。
本条记录仅表示启动；实时进度以该日志及对应inference_logs为准。
停止入口：bash ~/piper_runtime/inference_client/operate.sh stop。

### 用户结束连续执行后要求回零

已设置软件停止，连续客户端在KV等待中响应并退出。随后经用户授权恢复控制并
尝试左臂1%回零；左臂途中使能反馈丢失，自动电子停止，右臂回零未执行。
日志：../zero_checks/20261013_final_left/result.json（reached=false）。
随后只读状态：左J5未使能；右六电机全部未使能且为STANDBY/MOVE_P。
未确认使能丢失原因，未自动重使能或再次回零。已重新置软件停止标志。
本次不能记录为归零完成，需现场核查是否人为失能、急停或电源变化。

### 正常停止路径修复：与174退出行为对齐

已确认175将用户停止抛为普通异常后触发电子停止，是迁移额外改动。
新增StopRequested区分用户停止；在主循环、控制器检查、WebSocket等待中统一使用。
主入口捕获StopRequested/KeyboardInterrupt（含SIGINT/SIGTERM），仅执行连接清理，
不发送电子停止、reset、disable、关节或夹爪目标。保留原控制状态和最后目标，
不是停止瞬间锁住当前位置：尚未完成的目标可能继续执行，与174退出行为一致。
result.json以stopped_by_user=true、completed=false、error=null记录主动停止。
真实故障仍走故障急停路径，未声称全部故障行为与174相同。
25项离线测试通过，包括用户停止/信号不急停、故障仍急停；此次未运动验证。
此前出现的使能丢失仍待排查，修复停止代码不会自动恢复硬件使能。

## 用户手动执行：恢复使能并回零

统一入口位于~/piper_runtime/recover_and_zero.sh。
先确认现场具备运动条件、急停已释放、无人拖动示教；此前使能丢失原因应先核查。

```bash
cd ~/piper_runtime
bash recover_and_zero.sh --check
bash recover_and_zero.sh --execute
```

默认--check只读，不使能、不清停止标志；若CAN尚未启动，先单独执行bash can_up.sh。
--execute会拒绝已运行的推理/回零/恢复进程；激活CAN（必要时sudo询问密码），
先预检双臂，再直接使能（默认不reset）、CAN/MOVE_J，依次左臂/右臂1%回零。
回零不动夹爪、不写零点校准；最终核对双臂全使能且关节距零不超过0.5°。
正常完成保持使能和零位目标，不急停、不复位、不失能，不启动推理。
结束时重新开启软件停止标志，防止下一次误启动客户端；该标志本身不发送硬件急停。
后续准备运行推理时再显式operate.sh resume。

故障或回零超时立即结束，不自动重试；go_zero.py 已于2026-10-14取消异常及Ctrl+C时自动电子停止，改为置位软件停止并断开连接。已接收目标可能继续执行，不能把Ctrl+C当作“锁位暂停”。
现场紧急情况使用硬件急停。不要在使能丢失后反复重跑。
日志：~/piper_runtime/recovery_checks/时间戳_recover_and_zero_进程号/，
含console.log、恢复反馈、两臂回零结果和最终状态；执行出口另存exit_code.txt。
本次仅部署脚本并做只读检查，运动由用户现场执行。

### 19:18 用户授权执行恢复回零脚本

运行recover_and_zero.sh --execute。双臂预检通过；恢复左臂reset后在使能循环检查中
检测JOINT_COMMUNICATION_ERR(0x5)，六关节通信错误位均true，脚本中止。
未发送任何回零/夹爪目标，右臂未恢复。日志：
../recovery_checks/20261013_191857_recover_and_zero_178572/。
随后只读复查两臂均NORMAL、通信错误位清除，但仍全部未使能、STANDBY。
故障发生在reset之后且随后消失，是否复位过渡状态尚未证实；不认定永久硬件故障。
未自动重试；软件停止标志保持true。此次恢复/回零未完成。

### 复位后的稳定反馈等待修复

recover_control.py不再reset后固定等待0.3秒，改为调用recovery_feedback.py：
单次reset后只读轮询最多5秒，要求新鲜且正常反馈连续稳定0.5秒才进入使能。
窗口内仅等待缺失/过期反馈或通信异常恢复；示教、限位或其他故障立即退出。
超时则中止，不重复reset、不在通信错误期间发送enable。
每臂等待过程保存为left/right_reset_feedback.jsonl。
28项离线测试通过；只读检查不能证明复位暂态原因，需下一次现场执行验证。

### 19:23 更新后再次执行

用户授权运行recover_and_zero.sh --execute。
日志：../recovery_checks/20261013_192328_recover_and_zero_187328/。
左臂reset_feedback_stable=true，随后使能阶段check_motion再次检测六关节通信错误，
脚本中止，未下发回零/夹爪目标，右臂恢复未开始。仅延长复位稳定等待未解决问题。
复查两臂NORMAL/STANDBY、全部未使能，通信错误位已清除；软件停止标志true。
不自动重试，不认定故障已修复，需进一步核查使能阶段的反馈与现场电源/急停条件。

### 19:25 用户授权单次重试

再次运行recover_and_zero.sh --execute，仍在左臂恢复/使能阶段check_motion报告
JOINT_COMMUNICATION_ERR(0x5)，六关节通信错误位全置位，自动退出。
未进入双臂回零，未自动追加重试。软件停止标志由退出处理重新置位。
日志：../recovery_checks/20261013_192557_recover_and_zero_192199/。

### 19:28 普通使能路径修复

只发送一次左臂enable，不reset、不发送运动目标，连续读取5秒。
实测约0.152秒六电机全部使能，无通信错误，电压23V，无欠压/过流等标志。
证据：../recovery_checks/20261013_192824_enable_diagnostic/feedback.jsonl。
这证明直接使能在本次成功，不证明此前复位相关异常的固件内部根因。

recover_control.py普通模式不再reset；发送一次enable后等待异步反馈，
要求全部使能稳定0.5秒，最多5秒，随后设置CAN/MOVE_J并验证。
故障检查保持严格；不连续重发enable或自动重试。
确需电子停止恢复时才显式使用--reset-controller；此前异常发生在该路径，
此选项不应作为普通未使能状态的默认处理。
recover_and_zero.sh使用修复后的普通路径，命令不变。本次未执行回零。

### 19:31 直接使能路径完整验收成功

用户授权执行recover_and_zero.sh --execute，未reset，双臂使能及CAN/MOVE_J恢复成功。
左、右依次1%回零成功，最终12关节均0°、全部使能、CAN_CTRL/NORMAL。
未发送夹爪指令或写零点标定，未启动实机推理；退出后软件停止标志true。
日志：../recovery_checks/20261013_193119_recover_and_zero_193090/。


## 2026-10-14 反馈异常下垂事件后的修复

旧版在执行中遇到任意 BaseException 时调用 electronic_emergency_stop，可能导致下垂。当前推理入口已取消此自动调用：异常立即终止动作循环，原子写入 emergency_stop=true，并断开连接。正常软件停止和 Ctrl+C 也置位该标志。反馈新鲜度、硬件故障、目标有限性及原有发送前检查仍保留；没有放宽反馈超时，也没有自动重试或恢复。

这不是位置保持实现：不再发送新指令并不能取消控制器已接收的目标，也不保证断连后姿态保持。恢复前需要在可靠支撑条件下验证控制器行为和处理 CAN 故障。现场急停能力未修改。go_zero.py 等独立工具不属于本次推理入口修复范围，仍需单独审查，不能据此认为已适合恢复运动。

结果文件新增 software_stop_latched、stop_latch_error、automatic_electronic_stop_sent、fault_response。视频或结果文件写入失败也会执行连接清理。WebSocket 请求发送前新增软件停止检查。

离线验证：33 项测试通过，覆盖反馈/网络/输入异常、用户停止，以及视频写入、结果写入和停止标志写入失败；无机器人 SDK 或硬件参与。175 部署后仅做语法检查，未使能、未运动。备份：backups/fault_stop_fix_20261014/。


### 2026-10-14 归零入口异常处理修复

go_zero.py 已单独修复：异常或信号中断后置位软件停止，停止发送目标，清理连接；不自动电子停止、reset、disable或恢复。结果写入失败也清理连接。离线模拟反馈异常及KeyboardInterrupt两种路径通过，175语法及导入检查通过；这不代表已验证硬件断连保持姿态。原脚本备份于backups/fault_stop_fix_20261014/go_zero.before_fix.py。
