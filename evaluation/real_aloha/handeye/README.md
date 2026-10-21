# 分体式 ALOHA 相机手眼标定

ROS 1 Noetic 采样，离线 OpenCV 求解。程序仅订阅话题；手动或通过已有遥操作系统移动机械臂。
支持腕部相机 `eye_in_hand` 和固定前置相机 `eye_to_hand`。
不包含相机内参标定、关节零点写入或 TCP 标定。

需要 Python 3.8+、numpy、scipy、带 aruco 的 OpenCV；采样还需要现有 ROS 环境中的
rospy、message_filters、cv_bridge、sensor_msgs 和 geometry_msgs。
先检查 `python -c 'import cv2; print(cv2.__version__, hasattr(cv2, "aruco"))'`。
若缺少 aruco，在兼容现有 cv_bridge 的环境安装 OpenCV contrib；不要混装多个 OpenCV wheel。
离线求解不需要 ROS。以下命令均在 Zero-WAM 根目录执行。

## 准备

1. 使用平整、刚性的 ArUco 标记，默认字典 DICT_4X4_50、ID 0。
   实测黑色外框边长（不含白边），单位米；打印缩放会改变实际尺寸。
2. 确认原始彩色图像和 CameraInfo 对应，包含有效内参，使用相同 optical frame。
   当前支持 plumb_bob/rational_polynomial，不支持鱼眼或把原始内参用于矫正后图像。
3. 末端话题须为 PoseStamped，位置单位米，四元数 xyzw，表示 base_from_ee。
   `--ee-frame` 是该话题实际代表的末端坐标系名称，务必核对驱动，不要猜测为夹爪中心。
4. 腕部相机：板固定在桌上，相机固定在所选手臂上。
   前置相机：板固定在所选手臂末端，相机与该臂基座相对固定。
   采样期间固定底盘及升降机构。改变安装位置后重新标定。

## 采样

左腕相机示例（话题和末端 frame 应按实际驱动替换）：

```bash
python -m evaluation.real_aloha.handeye collect \
  --mode eye_in_hand \
  --image-topic /camera_l/color/image_raw \
  --info-topic /camera_l/color/camera_info \
  --pose-topic /puppet/end_pose_left \
  --ee-frame YOUR_ACTUAL_EE_FRAME \
  --marker-size 0.08 --marker-id 0 \
  --output /tmp/aloha_left_wrist_001
```

前置相机改为 `--mode eye_to_hand`，图像及内参话题改为
`/camera_f/color/image_raw` 和 `/camera_f/color/camera_info`。
右腕相机使用 `/camera_r/...` 和 `/puppet/end_pose_right`。
CameraInfo 话题为示例，使用 `rostopic list` 核对。

每次变换姿态，保持静止至少 0.5 秒，再按 Enter 采一组。采集约 20–30 组，覆盖
工作区域、不同距离、绕不同轴的明显转动；不要只平移或只绕一根轴转动。
板应清晰完整可见，避免远距离、近乎正对相机的重复姿态和严重倾斜。
按 q 完成，输出 samples.json、各样本 JSON、原始及标记可视化 PNG。
每组数据即时保存；正常退出或 Ctrl-C 保存汇总文件。输出目录必须尚不存在。

程序检查图像/位姿时间差（默认 50 ms）、新鲜度（500 ms）、静止期间变化
（1 mm/0.5°）以及重投影 RMS（1.5 px）。这些是可调整的采样筛选默认值，
不是设备精度承诺。单个平面标记有姿态歧义，低重投影误差不能排除姿态翻转；
应检查输出 PNG 和最终残差，必要时增加板尺寸或采用多标记板方案。

## 求解与验证

```bash
python -m evaluation.real_aloha.handeye solve \
  --input /tmp/aloha_left_wrist_001/samples.json \
  --output /tmp/aloha_left_wrist_001/result.json
```

至少 15 组。每 5 组留出 1 组，不参与初次拟合，用于验证；最后用全部数据求结果。
输出平移 RMS/最大值（mm）、旋转 RMS/最大值（度）及变换矩阵。
`A_from_B` 表示将 B 系坐标变换到 A 系：`p_A = A_from_B @ p_B`。

- 腕部输出 `ee_from_camera`，使用 `base_from_camera = base_from_ee @ ee_from_camera`。
- 前置输出 `base_from_camera`，直接将相机系点转换到所选手臂基座系。
- camera 是 CameraInfo 对应的 optical frame，不是相机外壳或 camera_link。

结果始终保留 `verified: false`。残差是数据一致性指标，不能证明实物绝对定位精度。
先检查留出样本误差，再用独立位置实测验证。工具中心点、机械臂零点错误和标记尺寸
误差仍可能造成抓取偏差。程序不会修改现有 calibration.example.json 或运动配置。

算法参考：[OpenCV calibrateHandEye](https://docs.opencv.org/4.11.0/d9/d0c/group__calib3d.html)。
固定相机模式先将 base_from_ee 取逆，再使用同一手眼求解器。

测试：`python -m unittest discover -s tests -p test_aloha_handeye.py`。

## 左右机械臂基座标定

使用同一台固定前置相机，依次对左、右臂采集 `eye_to_hand` 数据并求解。
相机必须能观察两只手臂上的标记。可以将同一块板从左臂移到右臂，不要求两次安装位置
相同，但每一次采样期间板与对应末端必须保持刚性固定。
两次采样之间严禁改变相机或双臂基座安装位置、升降高度。
话题名称相同不能证明物理安装未变，必须在现场确认。

```bash
python -m evaluation.real_aloha.handeye collect \
  --mode eye_to_hand \
  --image-topic /camera_f/color/image_raw \
  --info-topic /camera_f/color/camera_info \
  --pose-topic /puppet/end_pose_left --ee-frame YOUR_LEFT_EE_FRAME \
  --marker-size 0.08 --output /tmp/aloha_front_left_001

python -m evaluation.real_aloha.handeye collect \
  --mode eye_to_hand \
  --image-topic /camera_f/color/image_raw \
  --info-topic /camera_f/color/camera_info \
  --pose-topic /puppet/end_pose_right --ee-frame YOUR_RIGHT_EE_FRAME \
  --marker-size 0.08 --output /tmp/aloha_front_right_001

python -m evaluation.real_aloha.handeye solve \
  --input /tmp/aloha_front_left_001/samples.json \
  --output /tmp/aloha_front_left_001/result.json
python -m evaluation.real_aloha.handeye solve \
  --input /tmp/aloha_front_right_001/samples.json \
  --output /tmp/aloha_front_right_001/result.json

python -m evaluation.real_aloha.handeye bases \
  --left /tmp/aloha_front_left_001/result.json \
  --right /tmp/aloha_front_right_001/result.json \
  --output /tmp/aloha_dual_bases_001.json
```

计算 `left_from_right = left_from_camera @ inverse(right_from_camera)`。
公共坐标系选为左臂基座，输出的 `base_transforms` 顺序为 `[左, 右]`，
因此第一项为单位矩阵，第二项为 left_from_right。
这不是底盘坐标系标定。如果要使用底盘或世界坐标系，还需要额外测量公共坐标系到左基座的变换。

程序检查相机 optical frame、话题及内参一致，并保留两次手眼验证误差。
输出不能直接当完整 robot_bridge 配置；其他字段仍需单独测量。
独立验证可用一个固定参考点和已标定 TCP，分别从多个姿态测量该参考点，
将两臂结果变到左基座系后比较偏差；选择符合实际任务的容差。
无需让双臂同时接触同一点。双臂误差还包含各自 TCP、机械零点及运动学误差。
