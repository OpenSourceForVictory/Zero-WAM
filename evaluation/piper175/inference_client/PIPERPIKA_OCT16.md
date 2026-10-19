# 10 月 16 日关节模型推理

2026-10-19：沿用 175 的原客户端执行流程（MOVE_J、6%、20 ms、spline、首 chunk 跳过首 latent、原始预测更新 KV 缓存）。参考“Zero-WAM 真机测试”会话及 evaluation/real_aloha/merged_client，未替换为分段诊断执行器。

新参数 `--action-contract piperpika` 要求 reset 返回 `piperpika_absolute_joint12_gripper2_v1` 和明确的通道顺序。输入是左关节 6、右关节 6、左夹爪、右夹爪；转为原控制器的左关节 6、左夹爪、右关节 6、右夹爪。关节为绝对弧度，夹爪为米，不再乘 0.8/0.9 或按 2 cm 阈值置零。现有固件夹爪限幅仍生效。

默认 `legacy174` 保留旧模型行为；遇到声明新动作合约的服务端会拒绝运行，避免误解析。`--gripper-mode legacy174` 是原有的“发送夹爪命令”开关，其名称为兼容旧脚本保留；具体缩放由 action-contract 决定，`hold` 仍不发送夹爪命令。

## 服务端

在模型服务器运行随附的 `start_piperpika_oct16_server.sh`，先确认设备 7、端口 29056 和分布式端口 29611 可用。脚本选择 best_model、joint_v2 数据根、20 去噪步和 2 chunk frames。服务仅监听 127.0.0.1，需沿用 SSH 隧道将工控机 39056 转发至服务器 29056。

## 工控机

目录 `/home/piperpika/piper_runtime/inference_client`：

```bash
# 默认只预测，不使能、不发送运动指令；仍需相机/CAN 就绪且没有停止标记
bash run_piperpika_oct16.sh --shadow

# 运动范围清空、硬件状态和预测已核对后，执行一个 chunk
bash run_piperpika_oct16.sh --execute
```

脚本不自动清除停止标记、不自动归零、不自动建立隧道。提示词固定为 `pick object to the container`，人类示教使用 group_001.pth，对应 `human_video/episode_20261016_210123_751155832/video.mp4`。额外客户端参数可追加。

原始预测保存在 prediction_XXXX.npy；日志中的动作诊断使用重排后的执行通道，reset 元数据也记录在日志中。更新 KV 缓存继续使用原始服务端通道顺序。

## 验证

本地 44 项测试通过（动作合约/通道映射/原始预测不变/旧模式/诊断/故障停止）。这不代表新权重已完成在线推理或真机执行验证。没有自动使能、清停止标记或发送运动指令。

在线预检发现服务端默认 guidance_scale=-1 会禁用文字条件。专用客户端脚本现在显式在 reset 中传 video_guidance_scale=1、icl_guidance_scale=5，与本次评估配置一致。

## 2026-10-19 在线验证

已部署到 192.168.137.54。服务 NPU 7、127.0.0.1:29056；工作站 49056 经 SSH 转发至服务器，54 的 39056 经 SSH 反向转发至工作站。相机曾返回 stale frame，重启 piper-cameras.service 后三路取帧恢复。清除了之前停止标记后启动本次会话。

文字条件启用的 shadow 通过；reset 确认 best_model、20 steps、chunk_frames=2、target_text_enabled=true、guidance=1/5。用户确认运动范围清空后执行一个 chunk，日志位于 54 的 `inference_logs/oct19_text_enabled_execute/20261019_093454_251550_execute`。预测22.67秒、执行2.12秒、KV更新43.26秒，总计68.05秒；输出(14,2,16)，原始32步平滑为47步，跳过首latent后发送23步。

两次到位等待返回false，采样最大误差18.22度，不能认定全程跟踪成功或任务成功。结束后独立只读检查显示双臂12个关节均使能，check_ready通过，末态关节接近最后目标；未发送归零或失能命令。客户端已正常退出，未执行第二个chunk。服务端仍运行。

## 完整录像（2026-10-19 增补）

专用启动脚本默认带 `--record-continuous`。相机录像独立子进程只读取已有的相机HTTP接口，不打开摄像头设备或CAN，不抢占控制。原生640×480、MJPEG AVI，目标30FPS、30秒分段；`continuous/timestamps.csv` 保存实际每帧采集和写入时间。重复采集时间戳不重复写入，因此实际帧率会略低于标称30FPS，精确时间分析应使用CSV，不能只按播放器时长推断真实执行时长。客户端正常退出/软件停止时会关闭录像文件；异常掉电可能损坏当前段，已关闭的段不受影响。

`continuous/high_00000.avi`、`left_00000.avi`、`right_00000.avi` 为连续录像；原 `cam_*.mp4` 仍只是执行关键帧。`continuous/result.json` 保存实际帧数与错误信息。录像启动失败阻止进入推理，录像进程退出会在下一chunk检查时结束客户端。

服务端补丁来源：`evaluation/piper175/server_support/piperpika_server.py`，部署到模型服务器 `evaluation/piperpika/server.py`。每次reset建立独立 `outputs/prediction_traces/piperpika_<时间>_<ID>` 目录，逐chunk原子保存latent与动作 `chunk_XXXXX.pt`。客户端的 `action_contract.jsonl` 和 `prediction_artifacts.jsonl` 保存对应服务端路径。

`server_support/decode_predictions.py` 部署到服务器 `evaluation/piperpika/decode_predictions.py`，由 `start_prediction_decoder.sh` 在单独空闲NPU（默认5）后台解码。每chunk生成按 high/left/right 横向排列的H264 CRF18 MP4，以及 `.decoded.json`；解码失败记录 `.decode_error.json`。解码不访问模型服务、不修改KV缓存。视频为逐chunk独立解码，保留chunk边界，不等于连续摄像头实拍；预测分辨率与帧率来自模型。停止后要等待最后的解码完成，勿在确认前删除PT。

完整执行：
```bash
cd /home/piperpika/piper_runtime/inference_client
bash run_piperpika_oct16.sh --execute --max_chunks 0
```

停止下发：另一个终端在同目录运行 `bash operate.sh stop`。保留使能，不自动归零或失能。下一次明确恢复使用 `bash operate.sh resume`。

正式带录像测试已于09:48启动（未限制chunk）。54日志：`inference_logs/oct19_full_recorded_test/20261019_094848_805101_execute`；服务器预测目录：`outputs/prediction_traces/piperpika_20261019_094850_8470f36c`。核查时三路连续AVI已生成两段且继续增长，首chunk的PT与MP4均已保存并解码成功。首chunk推理23.00秒、执行2.55秒、KV27.04秒；出现到位等待超时，尚不能判断抓取成功。检查时已进入第二chunk，测试保持运行。

## 320×240 请求图片（2026-10-19）

按用户要求，`format_obs` 在BGR→RGB之前用INTER_AREA将每路640×480缩到320×240；预测和KV请求使用同一个转换函数。三路图片原始数据由2,764,800字节降至691,200字节（原来1/4）。原始相机采集、连续录像及关键帧录像仍为640×480。manifest新增request_image_shapes区分采集与请求尺寸。原客户端备份在54的backups/oct19_joint_contract/client.before_half_resolution.py。

服务重启仍使用best_model、NPU7、20steps、chunk_frames=2；本次重启日志为outputs/piperpika_live_oct19/server_half_resolution.log。当前已验证的新隧道端口为54的39057，经工作站49057到服务器29056；group_002调用追加--server_port 39057。

## video=4 / action=10（2026-10-19 按命令行覆盖）

用户要求不修改配置文件。服务端仅通过如下命令重启：
`bash start_piperpika_oct16_server.sh --video-steps 4 --action-steps 10`
日志：`outputs/piperpika_live_oct19/server_video4_action10.log`。启动脚本和va_piperpika_train_cfg.py保持原样；覆盖参数优先于脚本的--steps。恢复客户端前要求reset回复video_inference_steps=4、action_inference_steps=10。
