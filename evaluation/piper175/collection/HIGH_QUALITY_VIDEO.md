# LeRobot v2.1 高画质视频试验

当前生产网页仍使用参考机默认 MPEG-4 编码。本改动提供可选的 `--video-quality high --video-crf 18`，不修改参考机复制脚本，不调整分辨率、帧率、动作布局或数据对齐方式。默认 `reference` 保留原编码。高画质模式使用 libx264、CRF 18、medium、yuv420p。

原始数据、HDF5 和既有转换结果必须保留。转换入口拒绝已存在的输出目录；编码器也拒绝覆盖文件。测试输入为单条 HDF5 的符号链接，原文件只读。

## 2026-10-17 验证状态

175 独立代码目录：`/home/piperpika/piper_runtime/staged/hq_video_20261017/`。

单条输出：`/home/piperpika/piper_runtime/recovery_checks/hq_video_20261017_155311/lerobot_h264_crf18/`。

使用 `episode_0.hdf5`，三路 640×480、30 FPS，各 777 帧。完整 `verify_lerobot.py` 校验通过：v2.1、28 维动作、777 条控制标签，三路各解码 777 帧。

随后 SSH 报连接超时/无法到达主机。与旧 MPEG-4 输出的画质指标比较尚未完成，不据此声称主观清晰度验收通过；尚未批量转换、尚未更改网页默认配置。

## 运行

在包含 reference_174、convert_lerobot.py、video_encoding.py 的代码目录下，使用 pika_export 环境：

```bash
python convert_lerobot.py --input_dir INPUT_HDF5_DIR --output_dir NEW_OUTPUT_DIR \
  --prompt 'pick object to container' --fps 30 --action-layout legacy28 \
  --version v2.1 --video-quality high --video-crf 18
python verify_lerobot.py INPUT_HDF5_DIR NEW_OUTPUT_DIR
python compare_video_quality.py SOURCE_EPISODE.hdf5 OLD_OUTPUT_DIR NEW_OUTPUT_DIR \
  --episode-index 0 --report NEW_REPORT.json
```

compare_video_quality.py 对三路视频逐帧解码验证帧数，每 30 帧以及中间/末帧与源 HDF5 做 RGB PSNR 比较，记录视频大小和编码。PSNR 用于辅助判断压缩损失，仍应查看相同时间点画面，尤其是物体边缘和纹理。CRF 18 仍是有损压缩，并非逐像素无损。
