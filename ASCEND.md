# Zero-WAM on Ascend 910B

本仓库保存适配代码、运行脚本、模型、示例输入和运行结果；Conda 使用命名环境 `zero-wam`，统一存放在 `/data5T/d00942093/miniforge3/envs/zero-wam/`。

**已验证（2026-09-29）：** 官方完整 checkpoint 在单张 910B3 上完成 50 步、连续两个片段的离线推理，动作与预测视频数值正常；每段约 14 秒，峰值分配内存 35.8 GiB。详细结果见 [RUN_REPORT.md](docs/ascend/RUN_REPORT.md) 和 [report.json](outputs/place_object_scale/report.json)。当前机器未运行闭环仿真。

## 环境与路径

- 项目目录：`/data5T/d00942093/zero-wam/Zero-WAM`
- Conda 环境：`zero-wam`（Python 3.10），路径 `/data5T/d00942093/miniforge3/envs/zero-wam/`
- 公共 Conda 安装：`/data5T/d00942093/miniforge3`
- 硬件：Linux aarch64，Ascend 910B3，8 × 64 GiB；推理默认仅使用 NPU 0。
- CANN：9.1.0；PyTorch：2.9.0；torch_npu：2.9.0.post6。
- Zero-WAM 上游提交：`08e2c4ae41e2b63573a299825cebe6753481407c`
- 官方模型：`robbyant-research/zero-wam-posttrain-robotwin`
- 模型版本：`07ee865f175d9474a5653e9147698390e47b6143`

新容器需以相同路径挂载本目录，并提供兼容的 ARM64 系统、昇腾驱动及 CANN。

```bash
source /data5T/d00942093/zero-wam/Zero-WAM/script/ascend/activate.sh
```

## 从头重建依赖

```bash
/data5T/d00942093/miniforge3/bin/conda create -y -n zero-wam python=3.10 pip
source /data5T/d00942093/zero-wam/Zero-WAM/script/ascend/activate.sh
python -m pip install -r /data5T/d00942093/zero-wam/Zero-WAM/requirements-npu.txt
python -m pip install -e /data5T/d00942093/zero-wam/Zero-WAM --no-deps
```

已经存在的环境直接激活即可。`deployment/ascend/requirements-installed.txt` 和 `deployment/ascend/conda-explicit.txt` 保存本次实际安装版本。原始 CUDA requirements 文件仍用于上游 CUDA 配置；NPU 使用单独的 `requirements-npu.txt`。

## 推理

```bash
cd /data5T/d00942093/zero-wam/Zero-WAM
./run_inference.sh --steps 50 --chunks 2
```

输入为 HumanGen 数据集 `place_object_scale` 的 episode 000071 三路相机视频，以及官方固定人类演示的预计算特征。默认使用 ICL CFG=5、target text CFG=-1、随机种子 0；图像尺寸和动作通道保持上游配置。

输出到 `outputs/place_object_scale/`：

- `actions.npy` / `actions_000.npy`：动作数组，通道、潜在帧、每帧动作。官方客户端跳过首块的第一潜在帧，并进行末端位姿处理；此文件没有执行任何机器人动作。
- `video_latents_000.pt`：预测视频 latent。
- `predicted_video.mp4`：VAE 解码的视频。
- `report.json`：版本、输入、步数、形状、耗时和 NPU 内存峰值。
- `preview.png`：本次验证提取的首、中、末帧预览；每行包含三路相机画面。

`--steps 1` 仅用于快速排查执行链路；标准验证使用默认 50 步。`--chunks 2` 使用录像后续帧验证观测缓存和连续调用。录像回放不是闭环仿真，不能据此报告任务成功率。

启动单 NPU WebSocket 服务：

```bash
./run_inference.sh --serve --port 29056
```

服务默认绑定 `127.0.0.1`，跨机器连接可配置 `--host`，详见下方部署说明。已实际验证健康检查、reset 和动作推理请求；临时测试服务已停止。复用原项目请求协议，由 `VA_Server.infer` 处理 reset、观测缓存和推理请求。单卡运行不需要 CUDA、NCCL 或 FSDP。

## 适配范围

- FlashAttention 改为可选导入；CUDA 路径保留。
- NPU ICL 推理使用 `torch_npu.npu_fusion_attention`；保留序列隔离、时间窗口、视频可访问 ICL、动作不可直接访问 ICL 等规则。
- 将旋转位置编码表示为实数 cos/sin 对，避免 NPU 上的 complex / float64 计算。
- 显式选择 `npu:0`，保留原默认 CUDA 行为。
- 仅适配推理；未适配 NPU 训练或多卡分布式。

## 验证

```bash
cd /data5T/d00942093/zero-wam/Zero-WAM
python -m pytest -q tests/test_npu_inference.py tests/test_icl_server.py tests/test_icl_model.py
```

NPU 测试比较融合注意力与 CPU SDPA 的数值，并验证全遮挡行、ICL/序列掩码及实数 RoPE。完整模型的 50 步、连续两片段运行已通过，保存的动作文件和 13 帧视频也已读回验证。相关上游测试 8 项通过、2 项因 CUDA 条件跳过；新增 NPU 数值测试 2 项通过。此处没有宣称完整训练测试套件通过。

## 仿真条件

当前机器不能按官方 RoboTwin 路径运行闭环仿真：

1. 官方指定 `sapien==3.0.0b1` 仅发布 x86_64 / Windows AMD64 wheel，ARM64 实际安装探测失败。见 `logs/simulation-probe.log` 和 `logs/sapien-pypi.json`。
2. 容器未提供 NVIDIA/AMD 图形设备或 Vulkan ICD；可见 `card0` 为 Huawei 设备（PCI vendor `0x19e5`, device `0x1711`）。昇腾 NPU 不提供该渲染后端。
3. RoboTwin 的 CuRobo 安装流程另有 NVIDIA CUDA 依赖。

本次交付目标因此为官方完整模型的 NPU 离线推理，不报告仿真成功率。`../RoboTwin/` 仅用于依赖检查，未声称已安装可运行的仿真环境。

参考：[RoboTwin 安装说明](https://robotwin-platform.github.io/doc/usage/robotwin-install.html)、[Ascend 版本匹配](https://github.com/Ascend/pytorch/blob/master/COMPATIBILITY.en.md)。

## 下载与完整性校验

```bash
python script/ascend/download_model.py
python script/ascend/verify_checkpoint.py
python script/ascend/download_inputs.py
```

模型下载支持断点续传和逐文件重试。`models/manifest.json` 保存官方文件尺寸与 LFS SHA256；`logs/checkpoint-verification.json` 保存全部校验结果。输入数据固定到 `data/input-manifest.json` 记录的版本，并校验 SHA256。

## 目录组织

- `script/ascend/`：环境激活、下载、校验及推理脚本；`run_inference.sh` 为推理入口。
- `docs/ascend/RUN_REPORT.md`：原始验证报告；文中的产物路径相对于仓库根目录。
- `models/`、`data/`、`outputs/`、`logs/`：本机模型、输入和运行产物；Git 仅保存下载清单及选定的小型验证记录。
- `deployment/ascend/`：历史依赖版本记录和原始适配补丁，供追溯使用；安装请使用当前 `requirements-npu.txt`，无需复制文件到外层。
- `/data5T/d00942093/miniforge3/envs/zero-wam/`：统一由 Conda 管理的命名环境。
- `../cache/`、`../RoboTwin/`：下载缓存和独立仿真仓库。

激活脚本按自身位置定位仓库。默认通过 `conda activate zero-wam` 激活环境；环境名和 Conda 安装目录可通过 `ZERO_WAM_ENV`、`ZERO_WAM_CONDA_ROOT` 覆盖。2026-09-29 的 JSON 和日志保留当时的原始路径；整理目录后的新运行记录使用新路径。

## 跨机器仿真

NVIDIA RoboTwin 客户端连接 910B 推理服务的部署与验证步骤见 [REMOTE_ROBOTWIN.md](docs/ascend/REMOTE_ROBOTWIN.md)。
