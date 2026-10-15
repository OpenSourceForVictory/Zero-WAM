# 175 客户端版本管理

2026-10-15 基线来自 175 的 `/home/piperpika/piper_runtime` 实际部署文件。
`deployment_snapshot.json` 记录取回时源码 SHA-256；本次没有修改控制逻辑或启动实机推理。
现用代码与 10 月 13 日工作区副本不同，以 175 部署版本为准。

## 仓库与版本

开发工作区：Zero-WAM 仓库的 `evaluation/piper175/`。
175 运行目录：`/home/piperpika/piper_runtime`，为独立 Git 仓库。
独立仓库通过开发仓库的 `git subtree split --prefix=evaluation/piper175` 提取，提交号与主仓库不同。
175 基线标签为 `piper175-baseline-20261015`，分支为 `main`。

```bash
cd /home/piperpika/piper_runtime
git status --short
git log -5 --oneline
git diff
```

修改后先检查差异和离线测试，再按文件提交：

```bash
git add <修改的源码或文档>
git commit -m "说明具体修改和原因"
```

不要在推理正在运行时切换版本。需要历史代码时可先创建独立工作目录：

```bash
git worktree add --detach ../piper_runtime-baseline piper175-baseline-20261015
```

新目录仅供比较/检查，不自动启动服务或连接机械臂。
日志、录像、备份、模型及实时 `inference_client/runtime_config.json` 不入库。
新部署首次使用前，在 inference_client 目录执行：

```bash
cp -n runtime_config.example.json runtime_config.json
```

示例默认停止；已有机器的实时停止开关不被版本管理覆盖。
环境和相机/CAN 安装见 DEPLOYMENT.md，操作记录见 inference_client/OPERATIONS.md。

## 当前 GPU 服务入口

在 175 的 `~/piper_runtime/inference_client`：

```bash
bash run_gpu_demo.sh --check
```

入口默认使用 `192.168.8.177:29056`，示教为 `bread_000`，服务端数据根目录
`/home/huawei/Datasets/aloha_zerowam_v3`。可用 ZERO_WAM_SERVER_HOST、
ZERO_WAM_SERVER_PORT、ZERO_WAM_SERVER_DATA_ROOT 和 ZERO_WAM_HUMAN_DEMO 覆盖。
这与原 Ascend SSH 隧道入口不同；本次 Git 整理未检查服务在线情况。
实际运动仍需原有显式 --execute 与现场运行条件，详见客户端说明。

## 离线验证

独立仓库可运行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ~/miniconda3/envs/piper_new/bin/python -m pytest -q inference_client/test_fault_stop.py inference_client/test_diagnostics.py
```

主仓库还包含 tests/test_piper175_*.py。离线测试不使能、不移动机械臂。
