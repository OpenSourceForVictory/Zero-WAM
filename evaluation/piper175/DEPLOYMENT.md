# Piper 新机器部署与175环境清单

核对日期：2026-10-13。这里描述现有客户端架构，不是所有Piper项目的通用要求。

## 175 实测环境

- Ubuntu 22.04.5 LTS，内核6.8.0-138-generic。
- Miniconda目录~/miniconda3；环境base、pika、piper_new。
- 当前程序使用piper_new，Python3.10.20。
- pyAgxArm1.0.0、python-can4.6.1、numpy1.26.4、scipy1.15.3、
  opencv-python-headless4.13.0.92、websockets16.0、msgpack1.1.2。
- piper_sdk0.6.1也已安装，但当前arm.py和迁移控制器使用pyAgxArm。
- ROS2 Humble已安装；当前控制和相机链路不依赖ROS。
- pip check报告OpenCV4.13要求NumPy>=2，与现有1.26.4不符。
  已实机运行不代表依赖声明一致；不要将这组版本当成干净环境锁文件。
  本次未改动现有环境。
- 双臂固件S-V1.7-3，pyAgxArm的default profile；两路SocketCAN，1Mbps。
- 三路Dabai DC1 RGB，V4L2/MJPG采集640x480，按序列号绑定角色。
- 用户systemd服务piper-cameras.service提供8080网页和无损/observation.npz。
- 代码~/piper_runtime；推理客户端~/piper_runtime/inference_client。
- 服务端远程运行模型；175经本地39056隧道访问。175不加载模型权重，
  客户端路径不需要CUDA/PyTorch或本地NVIDIA GPU。

## 新机器安装范围

推荐复现同一Ubuntu22.04/Python3.10架构。安装USB-CAN驱动支持、CAN工具、
USB视频支持、Miniconda和下列客户端依赖，无需照搬174的ROS1 Noetic环境。
若只更换机械臂而继续使用175，不必重装系统或Python；重新核对固件、USB绑定、
左右臂及夹爪行程，并完成状态、低速运动验收。

系统工具（新机器）：

```bash
sudo apt update
sudo apt install openssh-server can-utils iproute2 v4l-utils libusb-1.0-0
```

安装Miniconda后建立独立环境。以下为新环境安装约束方案，尚未在另一台新机验收；
OpenCV限制为低于4.12以避免照搬175的NumPy依赖冲突，安装后必须pip check：

```bash
conda create -n piper_new python=3.10 pip -y
conda activate piper_new
unset PYTHONPATH LD_PRELOAD
export PYTHONNOUSERSITE=1
python -m pip install 'pyAgxArm==1.0.0' 'python-can==4.6.1' \
  'numpy==1.26.4' 'scipy==1.15.3' 'opencv-python-headless<4.12' \
  'websockets==16.0' 'msgpack==1.1.2'
python -m pip check
python -c 'import pyAgxArm, can, cv2, numpy, scipy, websockets.sync.client, msgpack'
```

msgpack_numpy.py随客户端目录提供，无需另找同名包。仅网页查看不需要Tk；
若使用cameras.py桌面预览，需要可用的Tk和桌面DISPLAY，可在Conda环境安装tk。
验证通过后导出新机器自己的pip freeze和conda env export记录。

## 部署代码与本机绑定

将evaluation/piper175的程序文件复制到新用户~/piper_runtime，包含完整
inference_client目录（排除旧日志、__pycache__和旧备份）。
run.sh默认解释器路径为$HOME/miniconda3/envs/piper_new/bin/python，若安装位置不同需改。
不要复制旧runtime_config状态后直接运动；显式检查stop状态和现场条件。

必须按新设备修改：

1. arm.py与can_up.sh中的左右USB物理端口。不要将can0永久等同左臂。
2. 固件核对：site_controller.py、go_zero.py、recover_control.py目前限定S-V1.7-3，
   新固件必须按实际SDK支持适配，不能简单删除检查或盲用default profile。
3. cameras.py中的设备路径模板、序列号与ROTATION_DEGREES；
   inference_client/camera_manager.py中的SERIALS，以及主客户端manifest中的角色标识。
   模型输入顺序为顶部、左腕、右腕；网页排列为左腕、顶部、右腕。
4. 99-piper-cameras.rules中的新相机序列号和用户OWNER。
5. 相机安装角度、夹爪行程、左右映射和模型动作定义。关节回零不是零点标定。
6. 模型服务器地址/端口、任务提示词、示教latent路径；不能假定新场景适用bread_test000。

新机器相机服务安装（先编辑好规则，勿照抄175用户名和序列号）：

```bash
cd ~/piper_runtime
sudo install -m 644 99-piper-cameras.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=video4linux
mkdir -p ~/.config/systemd/user
install -m 644 piper-cameras.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now piper-cameras.service
```

这是用户服务；无人登录时是否运行取决于用户管理器/linger设置。
175 CAN与模型隧道尚未配置开机自启；重启后用operate.sh prepare恢复硬件通信，
并按inference_client/OPERATIONS.md恢复模型隧道。
若直接连接远端可达WebSocket服务，可用--server_host和--server_port，
无需复制当前经工作站中转的拓扑。远端另需模型权重、推理环境、示教和归一化数据；
本手册的Python依赖仅涵盖机器人端客户端，不涵盖模型服务器部署。

## 验收顺序与边界

1. CAN和三相机只读检查，核对物理角色、方向、最新帧。
2. 模型连接检查；shadow预测（无运动，但会reset模型会话）。
3. 现场具备运动条件后，确认控制模式和使能，分别低速点动、回零。
4. 单动作块检查实际到位，然后才进行连续任务验收。

操作脚本和所有历史试跑见inference_client/OPERATIONS.md。
175已验证单块末目标到位及连续运行；完整breakfast任务成功未验收。
最后一次结束后回零遇到使能丢失，左臂J5/右臂全电机未使能，归零未完成；
软件停止标志已置位，不能把此状态当作下一次可直接运行的状态。

## 已核实的模型服务端启动方式

服务端主机1.95.69.29，当前使用服务器原本已启动的进程，迁移175时未重新启动模型。
在服务端启动命令为：

```bash
cd /mnt/sfs_turbo/lijianwen/Codes/Zero-WAM
bash start_infer.sh
```

start_infer.sh内容（将原脚本同一行的export拆行展示，含义相同）：

```bash
export ZERO_WAM_PREDICTION_ROOT="$PWD/outputs/prediction_traces"
export ALOHA_PREPARED_ROOT=/mnt/sfs_turbo/lijianwen/Datasets/aloha_zerowam_v3
export OMP_NUM_THREADS=4
export ASCEND_RT_VISIBLE_DEVICES=7
export MODEL_PATH="$PWD/outputs/aloha_step6000_validation/model"
export PORT=29056
bash evaluation/aloha/launch_server.sh --steps 50 --chunk-frames 2
```

底层使用/mnt/sfs_turbo/public/apps/miniforge3/envs/lingbot-vggt/bin/python，
以torch.distributed.run --nproc_per_node=1 --master_port=29611启动
-m evaluation.aloha.server，监听127.0.0.1:29056。
ASCEND_RT_VISIBLE_DEVICES=7选择昇腾设备7；不要将机器人端环境与此模型环境混淆。
预测记录目录为outputs/prediction_traces；已运行进程的标准输出指向
outputs/infer_logs/start_infer_video_trace_20261012.log。
此日志重定向来自启动外层，不是start_infer.sh本身的命令。

客户端通过SSH隧道访问模型。run_human_demo.sh设置ZERO_WAM_ICL_LATENT_PATH，
va_client.py的reset请求携带reset=true、prompt、use_icl=true、icl_latent_path。
该路径为服务端已有的人类示教latent .pth文件，不是上传原视频或175的本地路径。
更换示教由新的reset请求选择，无需因此重启模型服务；reset也重置当前推理会话。
模型权重/推理步数等启动配置变化则是另一类操作，不能混同示教切换。
