# Ascend 推理验证报告

日期：2026-09-29（UTC）。产物路径均相对于 Zero-WAM 仓库根目录；原始日志内的绝对路径保留运行时记录。

## 结论

Zero-WAM 官方完整 Robotwin checkpoint 已在 Linux ARM64、Ascend 910B3 上跑通离线 ICL 推理。本次使用单卡 NPU 0、BF16、seed=0、ICL CFG=5、target text CFG=-1；视频分支和动作分支均使用默认 50 步。

- 连续片段数：2，包含首帧图像编码、ICL 缓存、观测缓存更新、视频预测、动作预测和 VAE 视频解码。
- 动作文件：`outputs/place_object_scale/actions.npy`，float32，形状 `(16, 4, 16)`，全部有限值。
- 视频文件：`outputs/place_object_scale/predicted_video.mp4`，13 帧、864×224、三路相机并排。
- 模型加载：15.30 秒。
- ICL 缓存初始化：1.77 秒。
- 两个片段推理：13.96、13.49 秒。
- PyTorch 分配内存峰值：35.77 GiB；缓存预留峰值：38.68 GiB。不包含驱动自身占用。

上述时间是本次两片段测量，未作吞吐量或任务成功率基准。后续观测来自公开录像，未执行预测动作，因此不属于闭环仿真。

## 证据

- `outputs/place_object_scale/report.json`：完整推理参数和计时。
- `outputs/place_object_scale/artifact-validation.json`：保存后的动作、视频读回检查。
- `logs/inference-50steps.log`：真实运行日志，包含 ICL 和观测缓存计数。
- `logs/checkpoint-verification.json`：全部模型文件尺寸、所有 LFS 权重 SHA256 验证通过。
- `logs/test-npu.log`：2 项 NPU 数值测试通过。
- `logs/test-upstream-inference.log`：8 项相关上游测试通过，2 项 CUDA 测试跳过。
- `logs/tiny-model-comparison.json`：同一小模型权重的 CPU/NPU 视频、动作输出对照，最大绝对差 0.00390625。
- `logs/vae-probe.json`：官方 VAE 连续编码与五帧解码通过。
- `logs/server-probe.json`：在 NPU 1 上独立测试单卡 WebSocket 服务；健康检查 200、reset 和一次 1 步推理成功。临时服务已停止。
- `logs/pip-check.log`：独立 Python 环境的依赖一致性检查通过；检查时排除了系统 CANN 的 Python 包元数据。

`torch.__version__` 显示 `2.9.0+cpu`，这是所用 PyTorch wheel 的版本后缀；NPU 执行由 `torch_npu 2.9.0.post6` 提供，实际推理设备为 `npu:0`。

## 仿真限制

未跑 RoboTwin 闭环仿真：指定 SAPIEN 3.0.0b1 没有 ARM64 wheel，实际安装探测失败；本容器也没有所需的 NVIDIA/AMD 图形后端和 Vulkan ICD。仓库外 `../RoboTwin/`仅用于依赖检查。

官方参考：https://robotwin-platform.github.io/doc/usage/robotwin-install.html

## 复现

```bash
cd /data5T/d00942093/zero-wam/Zero-WAM
./run_inference.sh --steps 50 --chunks 2
```

复用环境、模型下载、服务启动和文件说明见 [ASCEND.md](../../ASCEND.md)。本次未引入 OpenSpec；通过固定版本、独立环境、适配补丁、可执行脚本和上述验证报告记录工作。
