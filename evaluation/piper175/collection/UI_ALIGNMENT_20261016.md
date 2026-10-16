# 2026-10-16 采集界面和参数对齐

当前地址：Pika 192.168.137.54:8081；参考 Aloha 192.168.137.160。
参考来源：/home/agilex/cobot_magic/collect_data/templates/index.html、web_server.py、collect_interactive.py 和 static 中的 Vue/Tailwind 文件。

已移植浅色布局、数据管理/采集分页、交互采集会话、Enter 开始/保存、D 丢弃/删除最近一集、最大帧数自动保存。保留单 Sense 切换左/右臂、暂停跟随，以及 Q 退出会话。退出会话暂停跟随并丢弃尚未保存的条目；删除移入回收站。双臂和三路图像始终共同记录。

读取参考机器 web_config.json 得到 frame_rate=30、max_timesteps=5000。Pika 持久配置 ~/datasets/pika_piper/.console_config.json 已设 fps=30、max_frames=5000；网页/服务端默认值、双臂采集 profile、转换默认帧率同步更新。30 Hz 对应每条约 166.7 秒。旧数据仍保持原来的 10 Hz，转换旧数据需选择 10 Hz，不得混合帧率转换或改写旧数据帧率。

原始 HDF5 保留 14 维实际下发目标，转换按既定 legacy28 从实测关节/末端位姿构造 28 维动作，输出 LeRobot v3。未移植参考机器的升降控制和推理页。

部署目录：~/piper_runtime/collection；服务：systemctl --user restart piper-collection-web.service。重启前确认无控制或采集会话。
本次参数修改前备份：~/piper_runtime/backups/capture_config_20261016_152545。
先前已保存版本：collection_20261015_213731_1f22008。首次界面对齐时额外备份命令因远端没有 python 命令而失败，原先保存的版本仍在；后续命令使用 pika_export 环境的绝对 Python 路径。

验证：43 项本地测试通过，含连续两条自动保存、会话锁、路径校验；浏览器模拟接口 14 项快捷键测试通过。实际网页三路实时画面可用；既有 128 帧、10 Hz 轨迹的元信息和三路图片读取成功。部署后 API 确认 30 Hz / 5000 帧。尚未开展 30 Hz 真机连续录制的吞吐与同步验收，本次未驱动机械臂。
