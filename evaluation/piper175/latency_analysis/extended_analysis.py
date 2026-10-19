"""Reproduce latency report from saved logs and the independent network probe."""
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


def mode(run):
    f = run['folder']
    if 'video4_action10' in f:
        return '320x240 / video4 action10'
    if 'half_resolution' in f:
        return '320x240 / video20 action20'
    return '640x480 / video20 action20' + (' / train' if 'full_recorded' in f else '')


def main():
    runs = json.loads((ROOT / 'runs_20261019.json').read_text())
    chunks = json.loads((ROOT / 'chunk_timings_raw.json').read_text())
    probes = json.loads((ROOT / 'network_probe_results.json').read_text())
    groups, first, rows = {}, [], []
    for r in runs:
        name = mode(r)
        rpc = r['rpc']
        pk = [x for x in rpc if x['kind'] in ('predict', 'kv')]
        assert all(x['kind'] == ('predict' if i % 2 == 0 else 'kv') for i, x in enumerate(pk))
        ps = [x for x in rpc if x['kind'] == 'predict']
        ks = [x for x in rpc if x['kind'] == 'kv']
        cs = chunks[r['remote_path']]
        restart = 'after server restart' if r['run'][9:15] in ('110925', '115618') else 'warm reset / earlier run'
        first.append(dict(run=r['run'], mode=name, state=restart,
                          reset_s=next(x['total_s'] for x in rpc if x['kind'] == 'reset'),
                          predict_s=ps[0]['total_s'], kv_s=ks[0]['total_s'],
                          execute_s=cs[0]['execute_s']))
        group = groups.setdefault(name, dict(predict=[], kv=[], wait=[], execution=[], subtotal=[], pack_predict_ms=[], pack_kv_ms=[]))
        for i, p in enumerate(ps):
            k = ks[i] if i < len(ks) else None
            c = next((c for c in cs if c['chunk'] == i + 1), None)
            row = dict(run=r['run'], mode=name, chunk=i + 1, phase='first' if i == 0 else 'later',
                       predict_s=p['total_s'], kv_s=k['total_s'] if k else None,
                       execution_s=c['execute_s'] if c else None,
                       measured_subtotal_s=sum(c[x] for x in ('infer_s', 'execute_s', 'kv_s')) if c else None)
            rows.append(row)
            if i:
                group['predict'].append(p['total_s'])
                group['pack_predict_ms'].append(p['pack_s'] * 1000)
                if k:
                    group['kv'].append(k['total_s'])
                    group['pack_kv_ms'].append(k['pack_s'] * 1000)
                    group['wait'].append(p['total_s'] + k['total_s'])
                if c:
                    group['execution'].append(c['execute_s'])
                    group['subtotal'].append(row['measured_subtotal_s'])
    summary = {name: {k: stats(v) for k, v in group.items()} for name, group in groups.items()}
    network = {}
    for size in sorted({p['bytes'] for p in probes}):
        samples = [p['roundtrip_s'] for p in probes if p['bytes'] == size]
        network[size] = dict(roundtrip_s=stats(samples), effective_payload_mbps=stats([size * 8 / t / 1e6 for t in samples]))
    output = dict(first=first, later=summary, network=network)
    (ROOT / 'extended_summary.json').write_text(json.dumps(output, indent=2))
    with (ROOT / 'per_chunk_extended.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    def table(headers, body):
        return '<table><tr>' + ''.join('<th>' + html.escape(str(x)) + '</th>' for x in headers) + '</tr>' + ''.join('<tr>' + ''.join('<td>' + html.escape(str(x)) + '</td>' for x in row) + '</tr>' for row in body) + '</table>'

    content = ['<!doctype html><meta charset="utf-8"><title>推理时延分析 2026-10-19</title>',
               '<style>body{font:16px system-ui;margin:32px;max-width:1400px}table{border-collapse:collapse;margin:20px 0}td,th{border:1px solid #bbb;padding:8px;text-align:right}p{line-height:1.7}</style>',
               '<h1>推理时延分析 · 2026-10-19</h1>',
               '<p>历史 7 次运行。首轮与后续轮次分开；训练集仅作历史参考。全部时间单位为秒，除非明确标注 ms。停止时未完成的 RPC 不按零计入。不同运行的图像、轨迹、网络和缓存状态不同，并非严格 A/B 实验。</p>',
               '<h2>每次运行首轮</h2>', table(['运行', '配置', '启动状态', 'reset', '预测', '动作执行', 'KV'], [[x['run'], x['mode'], x['state'], *[f'{x[k]:.3f}' for k in ('reset_s', 'predict_s', 'execute_s', 'kv_s')]] for x in first]),
               '<p>11:09 与 11:56 是服务端重启后的首次客户端运行；其余后续启动为热服务 reset。首轮 KV 上传 4 组观测，后续上传 8 组，不能直接用二者差值解释冷启动开销。</p>',
               '<h2>后续轮次统计</h2>', table(['配置', '指标', 'n', '均值', '中位数', 'P95', '最小', '最大'], [[name, key, s['n'], *[f'{s[k]:.3f}' for k in ('mean', 'median', 'p95', 'min', 'max')]] for name, group in summary.items() for key, s in group.items()]),
               '<p>predict/KV 是客户端 RPC 等待，包含网络、服务端处理和调度，不是纯 NPU 计算时间。execution 是客户端动作执行段耗时，不保证每个关节均已到达目标。subtotal 是预测＋动作执行＋KV 的已记录小计；不含段间保存、检查，以及最后等待到位和读取下一帧等，不能视为完整 chunk 周期。pack_* 单位为 ms。</p>',
               '<h2>同路径独立网络测试</h2>', table(['载荷字节', 'n', '均值 s', '中位数 s', 'P95 s', '最小 s', '最大 s', '中位有效吞吐 Mbps'], [[n, s['roundtrip_s']['n'], *[f'{s["roundtrip_s"][k]:.3f}' for k in ('mean', 'median', 'p95', 'min', 'max')], f'{s["effective_payload_mbps"]["median"]:.3f}'] for n, s in network.items()]),
               '<p>54 → 工作站 → 服务端，两段 SSH 隧道，独立端口 39058/49058/29058。WebSocket 无压缩，随机二进制载荷，服务端仅回复接收长度，不调用模型。64 字节为应用层往返延迟；其余为上传＋短确认时间，包含 SSH、WebSocket 和主机调度。大载荷每种只有 5 次，P95 仅供参考。它是当前测量，不能从历史 RPC 耗时中直接扣除来计算历史模型耗时。send() 返回可能只代表写入缓冲区，不能单独视为上传完成。</p>',
               '<p>数据来源：runs_20261019.json、chunk_timings_raw.json、network_probe_results.json。逐轮可查看 per_chunk_extended.csv；matched_chunks_2_to_17 位于 summary.json，便于在相同 chunk 范围比较。</p>']
    (ROOT / 'report.html').write_text('\n'.join(content))
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
