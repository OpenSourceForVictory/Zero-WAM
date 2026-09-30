# NVIDIA 仿真端连接 Ascend 推理端

首次部署请按 [NVIDIA 仿真端完整安装指南](NVIDIA_SIM_SETUP.md) 从代码获取、环境安装开始操作。

两台机器分别运行原项目 RoboTwin 仿真与单卡 Zero-WAM 推理。仿真端发送三路 RGB 观测、reset 和缓存更新请求，推理端返回原始动作段；末端动作还原和执行仍由原 RoboTwin 客户端负责。该流程未实现 Piper 真机控制。

## 910B 推理端

在当前 Zero-WAM 仓库运行：

```bash
./run_inference.sh --serve --host 0.0.0.0 --port 29056 \
  --steps 50 --icl-registry deployment/ascend/icl-registry.json
```

模型、CANN、`torch_npu` 和人类演示 latent 均留在此端。`--host` 默认是 `127.0.0.1`，跨机器时指定可达的监听地址。使用可信内网或 SSH 隧道；当前接口没有鉴权或 TLS。`/healthz` 在模型加载后可用，表示服务可连接，不代表当前会话空闲。

注册表是 `演示 ID -> latent 文件路径` 的 JSON。相对路径按 JSON 所在目录解析。当前仅注册已下载的 `place_object_scale` 演示，可直接运行该任务；其他任务须下载其对应特征并增加独立 ID，不能使用同一个演示冒充全部任务。此模式客户端只发送 ID，不需要下载演示视频或共享服务端文件系统。

每个服务进程只允许一个客户端，首次请求必须 reset。第二个客户端会被拒绝；请求超时后客户端关闭连接，不自动重发动作或恢复旧回合。并行实验应使用不同服务进程、端口和独立 NPU。

## NVIDIA 仿真端

通过 `git clone https://github.com/OpenSourceForVictory/Zero-WAM.git` 获取包含本次修改的代码，完整命令见上述安装指南。也可以把提供的 `zero-wam-remote-client.tar.gz` 复制到 NVIDIA 机器后解压（源码包不包含模型权重、环境或仿真资产）：

```bash
mkdir -p Zero-WAM
tar -xzf zero-wam-remote-client.tar.gz -C Zero-WAM
cd Zero-WAM
```

按 [INSTALL.md](../../INSTALL.md) 安装项目指定的 RoboTwin **`2eeec322`**、仿真依赖及资产，并按文档覆盖两项任务的评测文件。不要直接使用不固定版本的最新 RoboTwin。

激活该机器的 RoboTwin 环境后，在 Zero-WAM 根目录安装通信依赖：

```bash
python -m pip install -r evaluation/robotwin/requirements-remote.txt
export ROBOTWIN_ROOT=/absolute/path/to/RoboTwin
export HOST=YOUR_910B_IP
export PORT=29056
export ICL_ID=place_object_scale
export TEST_NUM=1
curl --fail "http://${HOST}:${PORT}/healthz"
bash evaluation/robotwin/launch_client.sh results/remote-place-object-scale place_object_scale
```

仿真端不需要下载 Zero-WAM 权重或安装 `torch_npu`。保留 RoboTwin 自身所需的 CUDA/PyTorch 环境。脚本默认使用 `demo_clean`、seed 0、原项目动作转换和评测循环。指标和对比录像写入指定 `results/` 子目录；`_result.txt` 与环境录像另保存在 RoboTwin 的 `eval_result/` 下，完整路径见安装指南。`REQUEST_TIMEOUT` 默认 300 秒，可按推理耗时调整。

未设置 `ICL_ID` 时保留原本的本地演示路径模式，配合原启动方式使用；启用了注册表的服务要求 reset 提供已注册 ID。原多 GPU 一键脚本未改造成跨机器调度器，请分别启动客户端和服务进程。

## 先验证通信，再跑仿真

可先从 910B 复制 `data/place_object_scale/` 中三路 MP4 到仿真端任意目录，再在仿真端运行：

```bash
python -m evaluation.robotwin.probe_remote \
  --host "$HOST" --port "$PORT" --icl-id place_object_scale \
  --input /path/to/place_object_scale --output results/remote-probe.json
```

此探测不导入 SAPIEN 或模型代码，实际发送 reset、两次动作推理及中间观测缓存更新，检查每次返回 `(16, 2, 16)` 且无 NaN/Inf。它是录像通信测试，不能代替闭环仿真或任务成功率评测。探测退出释放连接后，再启动 RoboTwin 客户端。

当前 910B 主机缺少可运行的 RoboTwin 仿真环境；NVIDIA 端安装、渲染和真实闭环任务仍需在部署后验证。机器人端的 Piper 适配将单独实现。

## 已完成的验证（2026-09-30）

- 4 项协议测试通过：服务端演示解析、数组传输、单会话隔离及重连 reset、请求超时关闭。
- 相关模型回归测试 10 项通过，2 项 CUDA 条件跳过。
- 官方完整模型在 `npu:0` 上经 WebSocket 完成 reset、50 步推理、观测缓存更新、第二次 50 步推理；两次动作形状均为 `(16, 2, 16)` 且数值有限。
- 使用独立客户端进程与服务端进程进行本机回环验证，尚未在 NVIDIA 主机或真实跨机网络上运行闭环仿真。
- 本机记录：`logs/test-remote-inference.log`、`logs/test-remote-regression.log`、`outputs/remote-validation/report.json`。临时验证服务已停止。
