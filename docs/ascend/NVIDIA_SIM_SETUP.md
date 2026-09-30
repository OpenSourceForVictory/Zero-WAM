# NVIDIA 仿真端安装与运行指南

本文部署 Zero-WAM 原项目的 RoboTwin 仿真客户端，连接另一台机器上的 Ascend 910B 推理服务。先运行 `place_object_scale` 一个任务、一个回合。机器人与场景沿用 RoboTwin 的 `demo_clean` 配置，此阶段不接入 Piper 真机。

本文核对了 RoboTwin `2eeec322` 的安装源码。2026-09-30 已在 RTX 4090、驱动 580.126.09、系统 CUDA Toolkit 12.4 上完成验证：PyTorch 2.4.1 cu121、PyTorch3D 和 CuRobo CUDA 扩展可用，SAPIEN 输出 `Render Well`，`place_object_scale` 单回合通过 SSH 隧道连接 910B 服务并成功完成。

## 1. 两台机器的分工

| 机器 | 安装与运行内容 |
| --- | --- |
| NVIDIA 仿真机 | Linux x86_64、NVIDIA 驱动与 Vulkan、CUDA Toolkit、RoboTwin、SAPIEN、CuRobo、远程客户端 |
| 910B 推理机 | 已安装的 `zero-wam` Conda 环境、Zero-WAM 权重、人类演示 latent、推理服务 |

仿真机不用安装 `requirements-npu.txt`，也不用安装 Zero-WAM 根目录的训练 requirements、FlashAttention 或下载 35 GB 模型权重。仿真机仍需要 CUDA/PyTorch 来运行运动规划等依赖。

以下按 Ubuntu 22.04、Python 3.10、CUDA Toolkit 12.1、PyTorch 2.4.1 编写。GPU 必须支持所需 Vulkan 渲染；RTX GPU 更适合此路径。A/H 系列数据中心卡需先验证渲染，不能仅以 `nvidia-smi` 正常作为依据。若 GPU 架构不受 CUDA 12.1/PyTorch 2.4.1 支持（例如更新的显卡），应先确定兼容软件栈，不要强行照用这些版本。

## 2. 通过 Git 获取代码

从本次适配使用的 GitHub fork 获取 `main` 分支：

```bash
export DEPLOY_ROOT="$HOME/zero-wam-sim"
mkdir -p "$DEPLOY_ROOT"
cd "$DEPLOY_ROOT"
git clone --branch main https://github.com/OpenSourceForVictory/Zero-WAM.git Zero-WAM
cd Zero-WAM
export ZERO_WAM_ROOT="$DEPLOY_ROOT/Zero-WAM"
git log -1 --oneline
git merge-base --is-ancestor 23ba594 HEAD
test -f evaluation/robotwin/requirements-remote.txt
test -f evaluation/robotwin/probe_remote.py
test -f docs/ascend/NVIDIA_SIM_SETUP.md
```

`git merge-base` 成功退出表示包含远程功能提交 `23ba594`。不要改为 clone 原作者的上游仓库；上游不包含本次 Ascend 和远程仿真适配。

如果已经从这个 fork clone 过，先在工作区无未保存修改的前提下更新：

```bash
cd "$DEPLOY_ROOT/Zero-WAM"
git switch main
git pull --ff-only origin main
export ZERO_WAM_ROOT="$DEPLOY_ROOT/Zero-WAM"
```

后续步骤使用 `DEPLOY_ROOT` 和 `ZERO_WAM_ROOT` 两个变量。它们可以按磁盘布局调整，Conda 环境统一通过名称管理。

## 3. 检查驱动、CUDA 和渲染能力

```bash
uname -m                          # 本指南要求 x86_64
nvidia-smi
nvcc --version                   # 本指南使用 CUDA Toolkit 12.1
sudo apt-get update
sudo apt-get install -y git curl build-essential cmake ninja-build pkg-config \
  unzip ffmpeg libvulkan1 mesa-vulkan-drivers vulkan-tools libgl1 libglib2.0-0
vulkaninfo --summary
ffmpeg -version
```

`vulkaninfo` 应识别到实际 NVIDIA GPU；只出现 llvmpipe/软件渲染不算通过。`nvidia-smi` 中的 CUDA Version 是驱动能力信息，不表示已经安装 `nvcc`。

若系统没有 CUDA Toolkit，请按 [NVIDIA CUDA Linux 安装说明](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/) 安装与所选 PyTorch 匹配的 Toolkit。下列路径按 CUDA 12.1 的常见安装位置设置，需确认目录实际存在：

```bash
export CUDA_HOME=/usr/local/cuda-12.1
export PATH="$CUDA_HOME/bin:$PATH"
nvcc --version
```

如果使用 Docker，需在**创建容器时**启用 `--gpus all` 和 `-e NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics`，使用带 CUDA 编译工具的 devel 环境。在容器内部也要重新验证 `nvidia-smi`、`nvcc`、`vulkaninfo`。仅提供 compute/utility 不足以运行 SAPIEN 渲染。

Isaac Sim 能运行不表示当前 shell 一定能找到 `nvcc`。Isaac Sim 自带运行时组件，而编译 PyTorch3D/CuRobo 需要系统 CUDA Toolkit。若 `/usr/local/cuda-*` 已存在，只需先修正 `CUDA_HOME` 和 `PATH`，不要重复安装 Toolkit。实测 PyTorch cu121 可由 CUDA Toolkit 12.4 编译这两个扩展；PyTorch 运行时仍报告 CUDA 12.1，这是 wheel 自带运行库的版本，二者可以共存。

## 4. 创建仿真环境并固定 RoboTwin 版本

先确保 `conda` 可用；没有时可按 [Miniforge 官方说明](https://github.com/conda-forge/miniforge)安装。不要复制 910B 的 ARM64 环境到 x86_64 机器。

```bash
conda create -n zero-wam-sim python=3.10 pip -y
conda activate zero-wam-sim

cd "$DEPLOY_ROOT"
git clone https://github.com/RoboTwin-Platform/RoboTwin.git RoboTwin
cd RoboTwin
git checkout --detach 2eeec322
export ROBOTWIN_ROOT="$DEPLOY_ROOT/RoboTwin"
git rev-parse --short HEAD
test -f script/requirements.txt
test -f policy/ACT/deploy_policy.yml
```

这个版本使用 **`script/`（单数）**，不是当前官网新版文档中的 `scripts/`，也不需要升级到新版 XPolicyLab 流程。

## 5. 安装 Python 依赖及 CUDA 扩展

使用指定版本的基础依赖；以下约束用于避免旧版 RoboTwin 被新的 NumPy、torchvision 等依赖拉向另一套环境。它们是部署基线，仍需通过后面的安装检查。

```bash
cd "$ROBOTWIN_ROOT"
python -m pip install --upgrade pip
python -m pip install 'setuptools<80' wheel ninja packaging
python -m pip install torch==2.4.1 torchvision==0.19.1 \
  --index-url https://download.pytorch.org/whl/cu121

cat > "$DEPLOY_ROOT/robotwin-constraints.txt" <<'EOF'
torch==2.4.1
torchvision==0.19.1
numpy==1.26.4
zarr<3
opencv-python<4.12
moviepy<2
scipy==1.10.1
scikit-image==0.21.0
warp-lang==1.5.0
EOF
export PIP_CONSTRAINT="$DEPLOY_ROOT/robotwin-constraints.txt"
python -m pip install -r script/requirements.txt
python -m pip install opencv-python pyyaml toppra
python -m pip install -r "$ZERO_WAM_ROOT/evaluation/robotwin/requirements-remote.txt"
python -c 'import torch; print(torch.__version__, torch.version.cuda); assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))'
```

RoboTwin 安装脚本还会安装 PyTorch3D、CuRobo，并修补 SAPIEN/mplib。这里拆开执行，便于确定失败位置，**无需再重复执行 `script/_install.sh`**。

```bash
# PyTorch3D：固定到核对时 upstream stable 指向的提交。
python -m pip install iopath fvcore
MAX_JOBS=4 python -m pip install --no-build-isolation \
  'git+https://github.com/facebookresearch/pytorch3d.git@75ebeeaea0908c5527e7b1e305fbc7681382db47'

# CuRobo：固定 0.7 系列作为旧版 RoboTwin 的部署基线，避免当前主线漂移。
cd "$ROBOTWIN_ROOT/envs"
git clone https://github.com/NVlabs/curobo.git
cd curobo
git checkout --detach v0.7.6
MAX_JOBS=4 python -m pip install -e . --no-build-isolation
cd "$ROBOTWIN_ROOT"
```

CuRobo 0.7.6 已在 RTX 4090 上验证。必须固定 `warp-lang==1.5.0`：CuRobo 的依赖只声明了 `warp-lang>=0.9.0`，会安装不兼容的新版本；例如 1.17.0 已不再提供该版 CuRobo 使用的 `warp.torch` 接口。不要忽略扩展编译失败继续运行任务。

按该版 RoboTwin 安装脚本的意图，修补依赖源码；脚本会保存首次修改前的备份：

```bash
python - <<'PY'
from pathlib import Path
import importlib.util

sapien_root = Path(importlib.util.find_spec('sapien').origin).parent
mplib_root = Path(importlib.util.find_spec('mplib').origin).parent
patches = [
    (sapien_root / 'wrapper/urdf_loader.py', '"r") as', '"r", encoding="utf-8") as'),
    (mplib_root / 'planner.py',
     'if np.linalg.norm(delta_twist) < 1e-4 or collide or not within_joint_limit:',
     'if np.linalg.norm(delta_twist) < 1e-4 or not within_joint_limit:'),
]
for path, before, after in patches:
    text = path.read_text()
    if before not in text:
        if after in text:
            print('Already patched:', path)
            continue
        raise RuntimeError(f'Unexpected dependency source: {path}')
    backup = path.with_suffix(path.suffix + '.robotwin-backup')
    if not backup.exists():
        backup.write_text(text)
    path.write_text(text.replace(before, after))
    print('Patched:', path)
PY
python -m pip check
```

## 6. 下载仿真资产、更新路径与评测文件

默认 `demo_clean` 只需要物体和机器人资产，不需要训练数据集，也不会读取随机背景纹理。背景纹理压缩包约 10.97 GB，仅在启用 `random_background` 时需要。下载默认任务所需资产：

```bash
cd "$ROBOTWIN_ROOT/assets"
python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="TianxingChen/RoboTwin2.0",
    allow_patterns=["embodiments.zip", "objects.zip"],
    local_dir=".",
    repo_type="dataset",
)
PY
unzip -n embodiments.zip
unzip -n objects.zip
cd "$ROBOTWIN_ROOT"
python script/update_embodiment_config_path.py

cp -n envs/move_stapler_pad.py envs/move_stapler_pad.py.upstream
cp -n envs/stamp_seal.py envs/stamp_seal.py.upstream
cp "$ZERO_WAM_ROOT/evaluation/robotwin/envs/move_stapler_pad.py" envs/move_stapler_pad.py
cp "$ZERO_WAM_ROOT/evaluation/robotwin/envs/stamp_seal.py" envs/stamp_seal.py
```

若要运行随机背景配置，再执行原下载脚本并解压背景包：

```bash
cd "$ROBOTWIN_ROOT/assets"
python _download.py
unzip -n background_texture.zip
```

默认配置应确认 `assets/objects` 和 `assets/embodiments` 存在；随机背景配置还应确认 `assets/background_texture`。路径更新脚本必须在 RoboTwin 根目录运行，移动整个安装目录后也需重新执行。任务文件覆盖是 Zero-WAM 原项目的评测要求。

## 7. 单独验证 CUDA 与 SAPIEN

```bash
conda activate zero-wam-sim
export PYTHONPATH="$ZERO_WAM_ROOT:$ROBOTWIN_ROOT:${PYTHONPATH:-}"
export MPLBACKEND=Agg
# 若 NVIDIA ICD 位于该路径，显式设置可避免 SAPIEN 误报找不到驱动。
export VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json
cd "$ZERO_WAM_ROOT"
python - <<'PY'
import torch
import sapien.core
import mplib
import cv2
from curobo.wrap.reacher.motion_gen import MotionGen
assert torch.cuda.is_available()
print('Imports and CUDA device OK:', torch.cuda.get_device_name(0))
PY
python -m evaluation.robotwin.test_render
```

应看到 `Render Well`。该上游渲染探测在失败时可能仍返回零退出码，所以要检查输出，不能只检查 `$?`。它验证渲染器初始化，最终相机出图还需要完整任务运行。无桌面服务器无需打开 Viewer，原 `demo_clean` 的 `render_freq: 0` 会离屏运行。

## 8. 在 910B 主机启动推理服务

这一节在 **910B 主机**执行，不在 NVIDIA 主机执行：

```bash
cd /data5T/d00942093/zero-wam/Zero-WAM
./run_inference.sh --serve --host 0.0.0.0 --port 29056 \
  --steps 50 --icl-registry deployment/ascend/icl-registry.json
```

保持该终端运行，或自行放在 tmux 会话中。注册表当前只有 `place_object_scale`，对应的模型和演示特征已在 910B 上下载。一个服务进程只接纳一个客户端，不能同时运行探测程序和仿真客户端。

当前协议无鉴权/TLS，使用可信内网或 SSH 隧道。`--host 0.0.0.0` 是监听地址，客户端 `HOST` 必须填写 910B 实际可达 IP。

## 9. 在 NVIDIA 主机连接并运行一回合

```bash
conda activate zero-wam-sim
export DEPLOY_ROOT="$HOME/zero-wam-sim"
export ZERO_WAM_ROOT="$DEPLOY_ROOT/Zero-WAM"
export ROBOTWIN_ROOT="$DEPLOY_ROOT/RoboTwin"
export HOST=YOUR_910B_IP
export PORT=29056
export ICL_ID=place_object_scale
export TEST_NUM=1
export SEED=0
export REQUEST_TIMEOUT=300
export MPLBACKEND=Agg
cd "$ZERO_WAM_ROOT"
mkdir -p results
curl --fail --connect-timeout 5 --max-time 15 "http://${HOST}:${PORT}/healthz"

set -o pipefail
bash evaluation/robotwin/launch_client.sh \
  results/remote-place-object-scale place_object_scale \
  2>&1 | tee results/remote-place-object-scale.log
```

健康检查预期输出 `OK`。首次仿真可能需要初始化或编译运动规划缓存；每次模型推理也有明显等待（已测 910B 50 步约十几秒），不能按实时控制速度判断程序是否卡住。

若公网端口不开放，可从 NVIDIA 仿真机建立 SSH 隧道。`ssh -N` 成功后会持续占用前台且不返回提示；加入 `-f` 可在认证成功后转入后台：

```bash
ssh -i /path/to/key -p 22 \
  -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -fN \
  -L 127.0.0.1:39056:ASCEND_LAN_IP:29056 SSH_USER@ASCEND_PUBLIC_IP

curl --noproxy '*' --fail http://127.0.0.1:39056/healthz
export HOST=127.0.0.1
export PORT=39056
```

使用 Clash TUN 等透明代理时，仅设置 `NO_PROXY` 不一定绕过系统策略路由。SSH 隧道可同时避免公开推理端口和透明代理路径问题。

任务首先筛选有效场景/专家轨迹，再调用远程策略。看到 `Success!` 或 `Fail!` 并生成评测文件，才说明一个闭环回合已执行结束；`Fail!` 可以是任务未成功，并不必然意味着通信失败。

结果分布在两个位置，这是原客户端行为：

- `Zero-WAM/results/remote-place-object-scale/stseed-*/metrics/place_object_scale/res.json`：成功数、总数、成功率。
- `Zero-WAM/results/remote-place-object-scale/stseed-*/visualization/place_object_scale/`：客户端对比录像。
- `RoboTwin/eval_result/place_object_scale/ACT/demo_clean/0/<时间>/`：`_result.txt` 和环境录像。

单回合正常结束后再增加 `TEST_NUM`，例如 10。一回合结果只用于部署验收，不构成稳定成功率评估。

## 10. 可选：先排除网络问题

如果希望先验证远程推理而不启动 SAPIEN，从 910B 复制三路样例录像：

```bash
mkdir -p "$ZERO_WAM_ROOT/data"
scp -r SSH_USER@ASCEND_IP:/data5T/d00942093/zero-wam/Zero-WAM/data/place_object_scale \
  "$ZERO_WAM_ROOT/data/"
cd "$ZERO_WAM_ROOT"
python -m evaluation.robotwin.probe_remote \
  --host "$HOST" --port "$PORT" --icl-id place_object_scale \
  --input data/place_object_scale --output results/remote-probe.json
```

预期两次返回形状 `(16, 2, 16)`、`finite: true`。这是录像回放，不是闭环仿真。探测退出后，再运行第 9 节。

## 11. 常见问题与验收记录

| 现象 | 检查位置 |
| --- | --- |
| 找不到 `--icl_id`、`probe_remote` | 下载的是上游旧代码，检查是否包含 `23ba594` |
| `scripts/_install.sh` 不存在 | 本文固定旧版，目录名为 `script/` |
| `nvcc` 不存在、CUDA extension 编译失败 | CUDA Toolkit、`CUDA_HOME`、编译器及 PyTorch CUDA 版本 |
| `Render Error`、Vulkan 找不到 GPU | 驱动图形能力、Vulkan ICD、容器 graphics 配置；`nvidia-smi` 正常不充分 |
| CuRobo 配置包含其他机器的绝对路径 | 在 RoboTwin 根目录重跑路径更新脚本 |
| 健康检查连接失败 | 910B 服务是否启动、监听地址、IP/端口、防火墙或网络路由 |
| `Unknown ICL ID` | 服务端加载了哪个注册表；目前使用 `place_object_scale` |
| 客户端一直等待连接 | 另一个客户端可能占用服务，关闭旧客户端后重启当前回合 |
| 推理超时 | 查看服务日志；客户端会关闭连接，新的会话必须重新 reset |
| 缺少物体、机器人或纹理 | 检查三个资产 ZIP 是否完整解压 |

成功部署后记录软件版本，便于复现：

```bash
cd "$ZERO_WAM_ROOT"
python -m pip freeze > results/nvidia-pip-freeze.txt
conda list --explicit > results/nvidia-conda-explicit.txt
nvidia-smi > results/nvidia-smi.txt
nvcc --version > results/nvcc-version.txt
git -C "$ROBOTWIN_ROOT" rev-parse HEAD > results/robotwin-revision.txt
git -C "$ROBOTWIN_ROOT/envs/curobo" rev-parse HEAD > results/curobo-revision.txt
```

参考：[RoboTwin 官方安装说明](https://robotwin-platform.github.io/doc/usage/robotwin-install.html)、[本次固定版本安装脚本](https://github.com/RoboTwin-Platform/RoboTwin/blob/2eeec322/script/_install.sh)、[固定版本依赖](https://github.com/RoboTwin-Platform/RoboTwin/blob/2eeec322/script/requirements.txt)、[PyTorch3D stable 安装要求](https://github.com/facebookresearch/pytorch3d/blob/75ebeeaea0908c5527e7b1e305fbc7681382db47/INSTALL.md)。官网已更新到新版，涉及目录和 API 时以本文固定的旧版源码为准。
