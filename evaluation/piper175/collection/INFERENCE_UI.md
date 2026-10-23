# 推理验证界面

入口：工控机 `http://192.168.137.54:8081` → **真机推理**。2026-10-22 已部署。

1. 选择人类示教：V01–V12 验证视频或 H_Txx_Sxx 的 32 段 10/20 训练示教。视频可预览（原始 MPEG-4 会生成 H.264 浏览器副本，不改变输入示教）；自动显示布局编号和对应长 prompt。当前选择的是已经编码的视频库，不是任意文件上传；新增视频须先编码并登记视频、latent 和 prompt 的对应关系。prompt 与既有编码绑定，界面只读，避免改了文字而 latent 仍携带旧文本条件。
2. 按示教布置物体，在机械臂控制页完成所需使能归零，退出采集会话和双臂控制。选择 29 或 R8000、检查/仅预测/真机执行模式。默认是检查模式，chunk 上限 0 表示连续运行；改为真机执行才发动作。核对现场状态后启动。

机械臂控制页的使能归零速度可选 1%、3%、5%、6%，页面默认 5%。双臂仍按左后右顺序回零，保持原故障检查、到位确认和夹爪不动。网页将所选速度传至 `enable_zero.py` 与 `go_zero.py`，结果 JSON 记录实际百分比。直接调用旧脚本而不指定 `--speed-percent` 时仍默认 1%，避免改变其他恢复脚本的行为。速度仅在下一次归零生效，不会改变正在执行的运动。
3. 停止或完成后，等待客户端保存视频。不会自动提交上传。用户在“结果上传（手动）”填写 29 的目标目录并点击“上传本次结果”，才提交解码及上传任务。目录在浏览器保存，刷新后仍保留。任务列表显示进度、失败原因和重试入口。

## 连接与部署

- 工控机网页及客户端：`/home/piperpika/piper_runtime/collection`、`../inference_client`；网页服务 `systemctl --user restart piper-collection-web.service`。
- 工控机 `collection/inference_demos.json` 为已核对的 44 条目录；`collection/inference_bridge.json` 存储归档服务地址及访问令牌，权限 600，不提交 Git。
- 示教视频预览首次由本机 broker 从 29 下载并转成浏览器可播放的 H.264；工控机将所得预览缓存在 `inference_client/demo_previews/<示教ID>.mp4`。后续请求由工控机本地文件提供，支持 HTTP Range（206）和浏览器缓存，切换或拖动播放位置无需再次经过本机 broker。首次未缓存的视频仍会受到 29 下载及转码耗时影响。
- 本机归档服务：`piper-inference-broker.service`，监听 `192.168.137.71:8092`。代码为本目录 `inference_broker.py`，配置在 `/home/dgh/.config/piper-inference/broker.json`（600）；任务状态与中间结果在 `/home/dgh/.local/share/piper-inference`。日志：`journalctl --user -u piper-inference-broker.service`。本机须保持在线，工控机才能准备示教和提交上传。
- 29：工控机 `127.0.0.1:39057` → 本机 `49057` → 29 `127.0.0.1:29056`，沿用已有隧道。本机 `zerowam-29-forward.service` 管理去 29 的连接。界面不启动、停止或重启模型服务，不修改模型配置。
- R8000：工控机直接访问 `192.168.137.223:29056`；准备时将所选示教 latent 同步到其 `outputs/ui_human_demos`。连接失败不自动换到 29。2026-10-22 已加载与 29 相同的 10/21 step 6000 权重，video 4 / action 10；最终以归档 `server_reset.json` 中的 model_path 和 step 为准。
- 环境及 SSH 地址在私有配置中维护。`inference_broker.example.json` 给出格式，不含口令。换 IP 时同时更新配置、工控机 bridge 和隧道。

R8000 新权重放在 `/data/Zero-WAM/models/piperpika_checkpoint_eval_20261021/best_model`。首次部署执行 `python3 prepare_r8000_oct21.py`，从已校验的权重契约生成 `/data/Zero-WAM/prepared/piperpika_10_16_10_20_zerowam_joint_v1` 所需元数据。`start_r8000_oct21_gpu.sh` 使用该元数据启动，服务单元为 `zero-wam-oct21-r8000.service`，监听所有网卡的 `29056`。在 R8000 执行 `systemctl --user status zero-wam-oct21-r8000.service` 查看状态，`tail -f /home/huawei/Zero-WAM/outputs/server_oct21_r8000.log` 查看日志。旧 RTC 服务 `zero-wam-rtc-model.service` / `zero-wam-rtc-runtime.service` 已停用，释放 GPU；与新服务不能同时装入 RTX 8000 的 48 GB 显存。新服务以模型目录的 `piperpika_action_contract.json` 对照准备元数据，检查通过后才接受请求。已从工控机确认端口可达，并在 R8000 上用 V01 latent 做不驱动机械臂的 reset：返回新模型路径、`checkpoint_contract_bound=true`、30 维归一化数组、video 4 / action 10、chunk 2。

2026-10-22 工控机部署前备份：`/home/piperpika/piper_runtime/ui_backups/20261022_135331`。部署文件为 `web_console.py/html`、`inference_console.py` 及客户端 `real_robot_inference_merged.py`、`inference_monitor.py`。与部署前客户端相比仅增加可选监控，不改变动作插值、执行步数或推理频率。

## 生命周期和结果

启动沿用 `inference_client/run.sh`，piperpika 动作协议，视频/示教 guidance 1/5。每次启动使用独立 `inference_logs/web/<时间>/`，保存 request.json、日志及客户端运行子目录。参数和归档目录由本次请求固定。更换示教需要重新确认布局。

普通停止和紧急停止均锁定软件停止，阻止后续动作；不失能、不下电，不承诺已接收目标瞬时停止。只发软件停止，不再叠加 SIGINT，以免二次中断视频封装。UI 显示进程退出前仍在停止中。硬件反馈故障可触发相同停止；遥操、归零、采集与推理互斥。

手动归档必须等客户端退出且存在 result.json；按 prediction_artifacts.jsonl 确认唯一服务端会话。29 在服务端使用 NPU 0 解码；R8000 的归档由本机 RTX 4090 解码：仅拉取该会话 reset.json 与 chunk_*.pt，校验本机 VAE 权重 SHA256 后用 CUDA 解码，再将预测视频上传 29。R8000 常驻的实时预测预览仍由原 CPU 解码服务负责。任务串行处理，失败保留所有源数据，服务重启后未完成任务标为可重试。

默认归档根目录：
`/mnt/sfs_turbo/lijianwen/Tests/real_aloha_test/R8000_261021_v4a10`

每次输出 `<服务器>_<人类示教编号>_<客户端运行名>/`：

- `real/combined.mp4`：三相机实拍关键帧拼接。
- `predictions/chunk_*.mp4`、`prediction_combined.mp4`：全部预测片段及拼接视频。
- `metadata/`、`server_reset.json`：prompt、示教 latent、实际模型、执行反馈、耗时和收到的 chunk 对应关系。
- `index.html`、`README.txt`、`verification.json`：查看入口与帧数、大小、SHA256 校验。

归档只复制本次运行的 combined.mp4、cam_high.mp4、cam_left.mp4、cam_right.mp4 实拍，不复制 continuous，不删除 raw、HDF5、原始视频或预测 latent。三路独立视频上传后逐个校验 SHA256，列在 verification.json 的 camera_videos 中。停止时最后一个服务端 chunk 可能没被客户端执行，校验记录同时保留预测总数与客户端收到数量。实拍与预测按各自帧率播放，不能按同一播放秒数认定动作对齐。

## 验证

单元测试覆盖既有控制台、推理生命周期、路径约束、归档重试/去重、未保存录像不能标为完成及停止不打断封装。实机部署检查仅访问网页/API和端口，不驱动机械臂。使用已结束的 20261022_133023 训练示例检验完整上传和预测解码，独立输出至 `real_aloha_test/ui_acceptance_20261022/`。

## 2026-10-22 实时画面与本次任务显示

推理页使用独立的实时刷新开关，不受采集页“实时流”开关影响。三路画面显示浏览器收帧时间，并提供重新连接按钮。页面顶部“本次执行任务”根据服务端状态中的 video_id 和客户端监控中的实际 prompt 显示本次任务，示教预览不随下面待启动配置改变。此次只热更新 HTML，不重启网页服务或推理进程。浏览器验证关闭采集实时流后推理三路仍在 4 秒内各收到 16 帧。

2026-10-22 上传改为完全手动：后端忽略旧页面传入的 auto_upload=true，结束推理不会自动发起上传。上传目录移出运行参数区域，可在推理期间编辑；只有运行结束、视频保存完成后才可上传。

## 2026-10-22 参数对齐核验（最近一次页面运行）

核验运行 `web/20261022_141447/20261022_141447_302608_execute`，服务端会话 `piperpika_20261022_141449_fef0300c`。页面 request.json、客户端 manifest.json、服务端 reset.json，以及实际 group_002.pth 内的 prompt/source_video 相符：H_T04_S02，10/20 human episode_20261020_194651_431424572，银色油壶→白篮、橘子→浅紫盘、土豆→深棕托盘。混合训练集 icl_manifest 的 Oct20 episode 3/4/5 也关联该视频。

29 正在运行的进程为 evaluation.piperpika.server，加载 piperpika_checkpoint_eval_20261021/best_model，video-steps=4、action-steps=10、chunk-frames=2、port=29056、NPU=7，prepared root 为 piperpika_10_16_10_20_zerowam_joint_v1。reset 返回 checkpoint_contract_bound=true；q01/q99 对应 12 关节及 2 夹爪的数值与权重目录 piperpika_action_contract.json 逐项完全一致。

客户端 execute、piperpika 动作契约、127.0.0.1:39057 隧道、max_chunks=0、guidance=1/5、原始三路640×480、请求三路320×240；执行速度默认6%。本次检查没有重启服务端或启动机械臂。此结论针对最近已提交运行；浏览器尚未提交的表单编辑无法从运行记录读取，R8000 当前实际配置未在此核验。

## 上传进度与目录命名

2026-10-22 更新：目录例如 `29_H_T04_S02_20261022_143252_960565_execute`，由本次 manifest 的 latent 路径匹配示教目录，不使用当前尚未提交的下拉选择。既有目录不重命名。示教原路径、prompt 保存在 metadata/demo_selection.json。

新上传任务每两秒刷新：接收实拍、检查、上传实拍、加载VAE与逐chunk解码/拼接、R8000结果传输（29跳过）、校验、索引保存。传输显示当前文件/批次字节数和速度；解码显示已完成/总chunk数；显示累计与阶段耗时、最新日志、后台心跳。百分比只代表当前传输，不冒充整项任务完成率。心跳只说明本地任务进程存活，不能当作远程模型进展。旧任务没有这些细节，不补造历史进度。

R8000 归档配置使用 `decode_device=cuda:0`、`local_python`、`local_decoder_script`、`local_checkpoint` 及 `local_checkpoint_sha256`；示例见 `inference_broker.example.json`。本机 decoder 使用本机权重解码，同时核对 latent 元数据中的服务端权重路径；归档记录同时保存服务端 `checkpoint` 与本机 `decoder_checkpoint`。29 使用其仓库的 `evaluation.piperpika.decode_predictions` 与 NPU。重试失败归档时保留原始真机视频与预测 `.pt`。R8000 实时 CPU 解码服务的配置与归档 GPU 配置相互独立。

2026-10-22 本机 4090 实测：三段预测、15 帧的归档解码与拼接用时约 6.1 秒；单段三视角解码约 0.60 秒（不含冷启动），峰值总显存约 22.1 GiB，当时已有约 17.1 GiB 被其他程序使用。服务端 CPU 在并发负载下一段约 37.9 秒；这些数字仅作该次负载下的参考，完整上传仍受 latent 下载和到 29 的网络传输影响。

R8000 常驻的 `zero-wam-prediction-decoder.service` 也须与当前权重一致；其启动脚本 `start_prediction_decoder_cpu.sh` 的 `--checkpoint` 现指向 `/data/Zero-WAM/models/piperpika_checkpoint_eval_20261021/best_model`，`decoder.env` 的 `DECODER_SINCE` 从新权重首次服务记录起算。切换权重时同时核对这两处，否则旧解码器会把新 `.pt` 标成 checkpoint mismatch。归档任务有独立解码队列，可在常驻解码器暂停时运行。

### 10 月 22 日示教与 prompt 对齐

直接逐段查看 V01–V12 人类示教的物体、容器和动作顺序，发现 V05、V08、V09、V10、V11、V12 的原表格 prompt 与视频不一致。具体原文、修正文及观察到的顺序保存在 `artifacts/zeroshot_oct21/demo_alignment_audit.json`。原始 `Vxx.pth` 均未覆盖；29 上新增 `outputs/zeroshot_long_oct21/aligned_prompt/Vxx_aligned.pth`，视频 latent 与原文件逐张量一致。修正版去掉错误的 `text_emb`，服务端 `_cache_icl_context` 会让示教侧继承本次 reset 的正确 prompt 编码。29 和 R8000 当前代码均已核对这一路径。R8000 准备示教使用带 `_aligned` 的独立文件名，避免读到旧缓存。归档服务在准备时也核对所选示教与 prompt；浏览器留有旧页面时会拒绝启动并提示刷新。

工控机 `collection/inference_demos.json`、本机归档服务目录、验证 manifest、配对表副本及布局指南已同步修正。当前 V05 旧 prompt 的运行 `web/20261022_213130/20261022_213130_621648_execute` 已通过软件停止；它不能用于评估模型遵循正确 V05 任务的能力。修改后须刷新界面重新选择并核对布局。32 条训练示教均按每段多个时间点抽样查看物体、目标容器和顺序，未发现同类不一致；这不等同于逐帧审核。

### 无文本 prompt 对照

推理页可关闭“发送文本 prompt”。此时客户端在 reset、预测观测和 KV 观测中使用空字符串；服务端要求空字符串而不接受 `null`。2026-10-23 的纯视频修订使服务端跳过本次文本编码，目标侧和示教侧统一使用模型配置中的固定 `empty_text_emb.pt`，并将 `video_guidance_scale` 设为 -1，ICL CFG 保持 5。归档服务从所选示教生成独立的 `no_prompt/*_noprompt.pth`，保留视频 latent、移除原有的 `text_emb`；服务端对空 prompt 请求也会强制覆盖示教侧文本 embedding。原始示教编码不覆盖。运行目录增加 `_with_prompt` 或 `_no_prompt` 后缀，上传目录也包含模式；历史页签和人工成功率按模式分别统计。旧记录按有 prompt 归类，检查/仅预测及未标记运行不纳入成功率。

2026-10-23 修复：旧版 `_reset_icl` 的 `if not prompt` 会拒绝空字符串。29 和 R8000 现允许在启用人类示教且指定视频/latent 时传入空字符串；仍拒绝 `null` 和同时缺少文字与示教的请求。部署此代码需要重启模型进程，之后每次通过网页切换模式仅需正常 reset。`tests/test_icl_server.py::test_video_only_reset_uses_fixed_empty_text_and_caches_demo` 覆盖固定空文本和示教缓存；`server_support/smoke_empty_prompt.py` 使用保存的相机观测执行真实 reset 和单 chunk 预测，不连接机器人硬件。历史的空字符串编码验证数据见下文，新版固定空 embedding 的验证数据见文末。

29 本机 WebSocket 同样通过空文本 reset（1.875 秒）和单块预测（11.537 秒），结果见 `artifacts/no_prompt_smoke_20261023/{r8000,29}.json`。检查时工控机的 29 隧道端口未监听，因此该次 29 验证不代表工控机到 29 的隧道可用。R8000 测试由工控机直接访问完成。

上传按钮下仅显示当前运行最近一次上传任务（按 run、服务器匹配，并取最新任务 ID）的进度日志。其他上传任务保留在默认折叠的“历史上传记录”，不混入本次日志；“历史推理运行”单独列出每次执行及其归档状态。无本次任务时明确显示尚未上传。

推理页现列出最近 50 条实际运行目录（`inference_logs/web`），每条读取 `request.json`、`manifest.json`、`result.json`，显示示教 ID、服务器、执行结果、下发步数与对应归档任务状态。尚无 `result.json` 的运行不允许归档；已结束且未归档或归档失败的运行可在历史行手动提交。归档按 run 和 profile 精确关联，不会把旧失败任务当作新运行结果。列表通过 `/api/inference/history` 提供，页面每两秒刷新。

历史真机执行支持人工标记“成功 / 失败 / 清除标记”，以及最多 1000 字的人工备注。可单独保存备注而不标记结果，也可填写备注后标记；清除成功/失败标记时保留备注，清空备注后保存才删除该人工记录。标记和备注写入该运行目录的 `manual_evaluation.json`，独立于客户端的 completed/stopped_by_user 和归档状态；页面仅对已结束的 execute 运行开放标记。`/api/inference/review-stats` 从所有已标记的历史 execute 运行计算成功率，未标记、检查和仅预测运行不纳入分母。标记可修改，页面每两秒刷新历史记录和汇总。历史运行的上传、失败重试与人工标记在另一条推理正在执行时仍可操作；但正在执行的运行须先写出 result.json 才可操作。后续归档会将已有标记作为 JSON 元数据复制；归档完成后再改标记不会自动改写 29 上既有归档快照。


2026-10-23 纯人类视频模式验证：选择“关闭发送文本 prompt”后，客户端仍用 `prompt=""` 标识本次请求；服务端的 `_reset_icl` 检测空字符串，直接加载 `empty_text_emb.pt`，跳过本次 T5 文本编码，并对 ICL 示教侧覆盖其内嵌文本 embedding。服务端强制 `video_guidance_scale=-1`、保持 `icl_guidance_scale=5`，reset 回传 `text_conditioning=video_only_fixed_empty_v1` 与 `target_text_enabled=false`。有 prompt 模式仍调用原文本编码器。29 和 R8000 的 `wan_va_server.py` 与 `evaluation/piperpika/server.py` 已部署并重启；原文件分别保存为 `.before_video_only_fixed_20261023`。使用保存的 V05 初始三相机观测，不接机械臂，各发一次 reset 和预测：R8000 reset 1.120 秒、预测 1.829 秒；29 reset 1.498 秒、预测 10.335 秒，均返回 14×2×16 有限动作。报告：`artifacts/video_only_fixed_20261023/{r8000,29}.json`。R8000 的 5 项服务端测试通过；29 的环境没有 pytest，以真实无动作预测验证。


2026-10-23 旧空 prompt 运行隔离：此前按“编码空字符串”协议录制的 13 次运行已从工控机 `inference_logs/web/` 原位移动到 `inference_logs/legacy_no_prompt_empty_encoded_20261023/web/`；原录像、反馈、人工评估和 request/manifest 均保留。旧目录 `index.json` 保存原路径到新路径映射及 10 条人工失败标记，原 `.inference_last.json` 备份为 `last_inference_state.json`。本机索引副本见 `artifacts/video_only_fixed_20261023/legacy_runs_index.json`。网页的无 prompt 统计从 0 次已评估运行重新开始，供固定空 embedding 协议的成功率测试使用。
