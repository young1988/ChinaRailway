#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 12306 车次清单转成本地可引入的 train_list_local.js。

数据源（二选一）：
  1) cr-12306-train-info 导出的扁平 JSON（train_list_YYYYMMDD.json）：
        [{date:"20260617", station_train_code:"K50", from_station:"齐齐哈尔",
          to_station:"杭州", train_no:"0400000K5011", total_num:"37"}, ...]
  2) 12306 官方 train_list.js（原生嵌套，var train_list = {日期:{类型:[...]}}）：
        直接原样搬运，只重写头部注释。

输出：railway-demo/train_list_local.js
      —— 与官方 train_list.js 完全同构（var train_list = {...}），
         静态页用 <script src="train_list_local.js"> 注入即可拿到全局 window.train_list，
         无需服务端、天然跨域。

为什么需要它：
  12306 官方 train_list.js 已停更于 2022 年（实测返回 2022-08 数据），不可用；
  cr-12306-train-info 公开快照目前最新到 2026-06-17（仓库已停更）。
  把它转成 train_list 格式后，前端即使没有 server.py 也能直接回答
  「这个车次在不在、起终站是哪」（经停站仍需服务端中转）。

用法：
  python3 tools/build_train_list_js.py <输入文件> [输出文件]
  默认输出 railway-demo/train_list_local.js
"""
import json, re, sys, os


def load(src):
    """返回 ('native', dict) 或 ('flat', list)。"""
    t = open(src, encoding='utf-8').read()
    if 'var train_list' in t or 'train_list=' in t:
        m = re.search(r'var\s+train_list\s*=\s*(\{.*\})\s*;?\s*$', t, re.S)
        if not m:
            raise SystemExit('无法解析原生 train_list.js')
        return 'native', json.loads(m.group(1))
    return 'flat', json.loads(t)


def flat_to_native(flat, date_override=None):
    nest = {}
    for r in flat:
        code = r['station_train_code']
        typ = (code[0] if code else '?').upper()
        raw = date_override or r.get('date') or ''
        if len(str(raw)) == 8:
            date = f"{str(raw)[:4]}-{str(raw)[4:6]}-{str(raw)[6:]}"
        else:
            date = str(raw)
        if not date:
            continue
        nest.setdefault(date, {}).setdefault(typ, []).append({
            'station_train_code': f"{code}({r['from_station']}-{r['to_station']})",
            'train_no': r['train_no'],
        })
    return nest


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'train_list_local.js')

    kind, data = load(src)
    if kind == 'native':
        nest = data
        snap = sorted(nest.keys())[-1]
    else:
        dates = sorted({r.get('date') for r in data if r.get('date')})
        raw = dates[-1] if dates else ''
        snap = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(str(raw)) == 8 else str(raw)
        nest = flat_to_native(data, snap)

    # 统计
    total = sum(len(v) for d in nest.values() for v in d.values())

    header = (
        "// train_list_local.js —— 本地车次清单（与 12306 官方 train_list.js 同构）\n"
        f"// 来源快照日期：{snap}\n"
        "// 由 tools/build_train_list_js.py 从 cr-12306-train-info 公开快照生成。\n"
        "// 这是「最近可公开获取的全量车次清单」：12306 官方 train_list.js 已停更于 2022 年。\n"
        "// 要换成真正当天的数据：导出 train_list_YYYYMMDD.json 后重跑本脚本即可。\n"
        "// 注意：本文件仅含 车次号 + 起终站 + train_no，不含完整经停站；\n"
        "//       完整经停站仍需 server.py 中转 12306 的 queryTrainInfo 接口。\n"
        "var train_list = "
    )
    with open(out, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write(json.dumps(nest, ensure_ascii=False))
        f.write(';\n')
    print(f"已生成 {out}")
    print(f"  快照日期: {snap} | 总车次: {total} | 文件大小: {os.path.getsize(out)//1024} KB")


if __name__ == '__main__':
    main()
