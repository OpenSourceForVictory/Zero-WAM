"""Summarize same-request client/server measurements; no robot access."""
import csv
import html
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent


def stats(values):
    a = np.asarray(values, dtype=float)
    return dict(n=len(a), mean=float(a.mean()), median=float(np.median(a)),
                p95=float(np.percentile(a, 95)), min=float(a.min()), max=float(a.max()))


def main():
    runs = json.loads((ROOT / 'server_timing_batch_raw.json').read_text())
    rows, first, groups, execution = [], [], {}, {'first': [], 'later': []}
    for run in runs:
        counts = {'predict': 0, 'kv': 0}
        for r in run['rpc']:
            kind = r['kind']
            if kind not in counts:
                continue
            counts[kind] += 1
            phase = 'first' if counts[kind] == 1 else 'later'
            s = r['server_policy_s']
            assert s is not None
            row = dict(run=run['run'], chunk=counts[kind], phase=phase, kind=kind,
                       total_s=r['total_s'], server_policy_s=s,
                       rpc_outside_policy_s=r['rpc_outside_policy_s'],
                       request_bytes=r['request_bytes'])
            rows.append(row)
            groups.setdefault((phase, kind), []).append(row)
            if phase == 'first':
                first.append(row)
        for c in run['chunks']:
            phase = 'first' if c['chunk'] == 1 else 'later'
            execution[phase].append(c['execute_s'])
    metrics = ('total_s', 'server_policy_s', 'rpc_outside_policy_s')
    summary = {phase: {kind: {k: stats([r[k] for r in groups[(phase, kind)]])
                            for k in metrics} for kind in ('predict', 'kv')}
               for phase in ('first', 'later')}
    result = dict(summary=summary, first=first,
                  execution={k: stats(v) for k, v in execution.items()})
    (ROOT / 'server_timing_batch_summary.json').write_text(json.dumps(result, indent=2))
    with (ROOT / 'server_timing_batch_per_request.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    table = [['阶段', '请求', '指标(秒)', '样本数', '平均', '中位数', 'P95', '最小', '最大']]
    for phase, kinds in summary.items():
        for kind, metrics in kinds.items():
            for metric, s in metrics.items():
                table.append([phase, kind, metric, s['n'], *[f'{s[k]:.4f}' for k in ('mean', 'median', 'p95', 'min', 'max')]])
    page = '<!doctype html><meta charset="utf-8"><title>首轮及非首轮服务端耗时</title><style>body{font:16px system-ui;margin:32px}td{padding:8px;border:1px solid #bbb}table{border-collapse:collapse}</style><h1>2026-10-19 同请求客户端／服务端计时</h1><p>三次热服务 reset，每次五个 chunk。服务端未重启。首轮和非首轮分别统计；轨迹和画面未固定，不是相同输入的性能基准。服务器 policy 计时排除上传和响应传输，但包含预处理及预测文件保存，不是纯设备计算计时。RPC 剩余耗时包含网络、编解包和调度，不能称为纯网络延迟。首轮 KV 4 组观测，非首轮 8 组。小样本 P95 仅供参考。动作等待到位超时需要单独审查，不代表动作成功。</p><table>'
    page += ''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in table)
    page += '</table><p>原始日志：server_timing_batch_raw.json；逐请求数据：server_timing_batch_per_request.csv。</p>'
    (ROOT / 'server_timing_batch_report.html').write_text(page)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
