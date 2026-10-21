# 工控机原版关节客户端

2026-10-12 从工控机 `/home/agilex/models/lingbot-va-piper/client` 导入。
`source_manifest.json` 保存来源和导入前 SHA256。主程序、控制器、相机、
平滑器及序列化代码保持原样。唯一适配是让 `va_client.py` 使用原目录的
`runtime_config.json`，与主程序的停止标志保持一致。

项目正式整段关节执行入口为 `evaluation/real_aloha/run_piper_joint.py`。
`run_piper_joint_probe.py` 是独立分段诊断工具，不是此入口的执行后端。

在工控机原有 ROS 环境及 `piper_new` 环境中，从项目根目录运行：

```bash
export ZERO_WAM_ICL_LATENT_PATH=/mnt/sfs_turbo/lijianwen/Datasets/aloha_zerowam_v3/human_latents/run_train/bread_000.pth
unset ZERO_WAM_ICL_ID
python evaluation/real_aloha/run_piper_joint.py \
  --prompt 'Place two plates on the tray, put bread on each, and add the metal cup.' \
  --control_mode movej --max_chunks 1
```

这条命令会使能并执行机械臂动作。示教路径指向服务端文件。沿用原客户端的
`127.0.0.1:39056`、`can_left/can_right` 和三路 ROS 相机话题。没有自动建立隧道。

行为与原代码一致：

- 控制器固件 profile 为 `default`（不是自动检测出的固件版本）。
- 默认 MOVE_J、6% 速度、20ms 普通步间隔、spline 平滑、速度约束重采样。
- 28 维动作取左关节 `[14:20]`、右关节 `[20:26]`，单位为绝对弧度。
- 第一个 chunk 跳过首个 latent 对应动作块；每个 chunk 完成后更新 KV 缓存。
- 夹爪取 `[26:28]`，左右分别乘 0.8/0.9，≤0.02m 置零，控制器限幅至 0.08m。
- 关键帧等待容差 3°；等待返回 False 时原循环仍继续。chunk 完成不证明全程跟踪到位。
- 原代码只在 chunk 开头检查 runtime 停止标志；不是逐步检查。
- 原代码 `logger.finish()` 被注释，不能依赖它生成最终统计和解码预测视频。
- `close()` 实际只断开连接，归零调用被注释；不会自动归零或失能。

本次保持上述行为以便复现原客户端，不将诊断工具的分段/超时急停流程混入。
尚未通过此项目入口执行实机测试；导入和离线测试不代表异常运动根因已经解决。
