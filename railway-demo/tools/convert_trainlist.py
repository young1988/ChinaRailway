"""
把 12306 车次列表 JSON 转成项目的 trains.js 种子格式。

输入（两种来源，自动识别）
--------------------------
1) HerbertHe/cr-12306-train-info 的发布物（推荐，含完整经停站）
   train_list_YYYYMMDD.json    车次清单：[{date, station_train_code, from_station, to_station, train_no}, ...]
   train_detail_YYYYMMDD.json  经停站明细：[{train_no, data:[{station_name, ...}]}]  ← 提供完整 stops
   仓库：https://github.com/HerbertHe/cr-12306-train-info （Release 里按日期发 train_list_/train_detail_）

2) 12306 官方每日车次文件（只有起终站，无经停站；当 cr-12306 停更时的后备）
   train_list.js  →  `var train_list = {"2026-10-28":{"G":[{station_train_code:"G1(北京南-上海虹桥)", train_no:"..."}]}}`
   注意：官方这个文件某些 CDN 会返回陈旧数据，stops 只能退化为 [from, to]。

输出
----
  railway-demo/trains.js  →  window.TRAINS_EXT = { trains:{ "G1":{from,to,stops,fetchedAt,line} } }
  与 index.html 里 TRAINS_BASE 的合并规则见 train_fetch.py 文档。

日期标注
--------
  * 默认从输入文件名里的 YYYYMMDD（或官方文件的日期键）自动识别「车次运行日期」，
    写进 queryDate / snapshotDate，并在文件头注释里**重点标注**（即用户要求的「1028 时间」）。
  * generatedAt = 本次转换执行的日期（默认今天）。
  * 想强制某个日期，用 --date YYYYMMDD。

station_name.js 后备能力
------------------------
  转换出的 trains.js 头部保留了一段**被注释掉的** station_name.js 加载说明，
  需要补全车站库时取消注释即可（见文件内「后备」块）。

用法
----
  python3 tools/convert_trainlist.py --list /path/train_list_20261028.json \
                                    --detail /path/train_detail_20261028.json
  python3 tools/convert_trainlist.py --list /path/train_list.js          # 官方文件，无经停站
  python3 tools/convert_trainlist.py --list train_list_20260617.json --detail train_detail_20260617.json --out ../trains.js
"""

import argparse
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 复用 train_fetch.py 的车站库 / 干线匹配逻辑（避免重复实现）
sys.path.insert(0, HERE)
import train_fetch as tf  # noqa: E402


def parse_list(path):
    """解析车次清单，返回 (日期文本YYYYMMDD, {code: {train_no, from, to}})。

    自动识别两种格式：
      * cr-12306：JSON 数组，字段 station_train_code / from_station / to_station / train_no / date
      * 官方 train_list.js：`var train_list = {"2026-10-28":{"G":[{station_train_code:"G1(北京南-上海虹桥)", train_no}]}}`
    """
    raw = open(path, encoding="utf-8").read()
    list_date = None
    out = {}

    if "train_list" in raw and "station_train_code" not in raw.split("=", 1)[-1][:200]:
        # 官方 JS 格式
        m = re.search(r"var\s+train_list\s*=\s*", raw)
        js = raw[m.end():] if m else raw
        js = js.strip()
        if js.endswith(";"):
            js = js[:-1]
        data = json.loads(js)
        # data: {date: {type: [{station_train_code, train_no}]}}
        # 选日期：--date 指定的，否则取字典里最大的日期键
        chosen = args_date_key(data.keys())
        list_date = chosen.replace("-", "")
        for typ, arr in (data.get(chosen) or {}).items():
            for e in arr:
                code, frm, to = _parse_official_code(e.get("station_train_code", ""))
                if not code:
                    continue
                out[code] = {"train_no": e.get("train_no", ""), "from": frm, "to": to}
        return list_date, out

    # cr-12306 JSON 数组格式
    data = json.loads(raw)
    if isinstance(data, dict):
        # 个别版本外层包了一层 {date, trains:[...]}，兼容一下
        data = data.get("trains") or []
    for e in data:
        code = (e.get("station_train_code") or "").strip()
        if not code:
            continue
        d = (e.get("date") or "").strip()
        if d and not list_date:
            list_date = d
        out[code] = {
            "train_no": e.get("train_no", ""),
            "from": e.get("from_station", "") or "",
            "to": e.get("to_station", "") or "",
        }
    return list_date, out


def _parse_official_code(s):
    """官方 station_train_code 形如 'G1(北京南-上海虹桥)'，拆出 车次 / 起 / 终。"""
    m = re.match(r"^([A-Za-z]+\d+)\s*\(([^()-]+)-([^()-]+)\)$", s.strip())
    if m:
        return m.group(1), m.group(2), m.group(3)
    # 退化：只有车次号
    m2 = re.match(r"^([A-Za-z]+\d+)", s.strip())
    return (m2.group(1), "", "") if m2 else (None, None, None)


def args_date_key(keys):
    keys = [k for k in keys if re.fullmatch(r"\d{4}-\d{2}-\d{2}", k)]
    if ARGS.date:
        cand = f"{ARGS.date[:4]}-{ARGS.date[4:6]}-{ARGS.date[6:8]}"
        if cand in keys:
            return cand
    return max(keys) if keys else None


def parse_detail(path):
    """解析经停站明细，返回 {train_no: [站名, ...]}。"""
    raw = open(path, encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get("trains") or []
    out = {}
    for e in data:
        tn = e.get("train_no") or e.get("train_no_full") or ""
        arr = e.get("data") or []
        stops = [s.get("station_name") for s in arr if s.get("station_name")]
        if tn and stops:
            out[tn] = stops
    return out


def ymd_to_iso(ymd):
    if not ymd:
        return ""
    ymd = ymd.strip()
    if re.fullmatch(r"\d{8}", ymd):
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ymd):
        return ymd
    return ymd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", required=True, help="train_list_YYYYMMDD.json 或官方 train_list.js")
    ap.add_argument("--detail", help="可选 train_detail_YYYYMMDD.json（提供完整经停站）")
    ap.add_argument("--date", help="强制标注的车次运行日期 YYYYMMDD（覆盖自动识别）")
    ap.add_argument("--out", default=os.path.join(ROOT, "trains.js"), help="输出 trains.js 路径")
    ap.add_argument("--keep-no-detail", action="store_true",
                    help="无 detail 时仍保留车次（stops 退化为 [from,to]）；默认丢弃")
    args = ap.parse_args()
    global ARGS
    ARGS = args

    station_names, alias = tf.load_station_names()
    tf.load_known_lines()
    print("车站库 %d 站名 / 别名 %d 条；预置干线 %d 条"
          % (len(station_names), len(alias), len(tf.KNOWN_LINES)))

    list_date_raw, listed = parse_list(args.list)
    print("清单解析：%d 个车次；清单日期 = %s" % (len(listed), list_date_raw or "未知"))

    detail = parse_detail(args.detail) if args.detail else {}
    print("明细解析：%d 条 train_no 经停站" % len(detail)) if detail else None

    snapshot_ymd = args.date or list_date_raw or ""
    snapshot_iso = ymd_to_iso(snapshot_ymd)
    generated_iso = date.today().isoformat()

    ok, bad, unknown_all = {}, {}, {}
    for code, meta in listed.items():
        tn = meta.get("train_no", "")
        stops = detail.get(tn) if detail else None
        if not stops:
            # 无明细：退化
            if not args.keep_no_detail:
                bad[code] = "无经停站明细"
                continue
            stops = [meta["from"], meta["to"]] if meta["from"] and meta["to"] else None
            if not stops:
                bad[code] = "无经停站且起终站缺失"
                continue
        rec = {
            "from": stops[0],
            "to": stops[-1],
            "stops": stops,
            "fetchedAt": snapshot_iso or generated_iso,
        }
        if meta.get("from") and meta.get("to") and (meta["from"] != stops[0] or meta["to"] != stops[-1]):
            # 起终站以明细为准，但记录清单里的名义起终（一般一致）
            pass
        lid = tf.guess_line(code, rec, None)
        if lid:
            rec["line"] = lid

        unknown = [s for s in stops
                   if s not in station_names and not (alias.get(s) in station_names)]
        if unknown:
            for u in unknown:
                unknown_all.setdefault(u, []).append(code)
        ok[code] = rec

    print("\n===== 汇总 =====")
    print("成功 %d / 丢弃 %d" % (len(ok), len(bad)))
    if bad and not args.keep_no_detail:
        print("丢弃原因：主要为「无经停站明细」（用 --detail 或 --keep-no-detail 可调）")
    if unknown_all:
        print("站名未收录 %d 个（画不出坐标，需补 stations_all.js / aliases.js）："
              % len(unknown_all))
        for u, cs in sorted(unknown_all.items(), key=lambda x: -len(x[1]))[:15]:
            print("   %-14s 出现于 %s" % (u, "、".join(cs[:3])))

    if not ok:
        print("\n没有可写出的车次，未生成文件。")
        return

    src_note = ("12306 官方 train_list.js（无完整经停站，stops 退化为起终站）"
                if detail is None else
                "cr-12306-train-info 发布的 train_list + train_detail")

    out = {
        "generated": "trains (convert_trainlist.py)",
        "source": src_note,
        "fetchedAt": generated_iso,
        "queryDate": snapshot_iso,
        "snapshotDate": snapshot_iso,
        "count": len(ok),
        "trains": ok,
    }

    header = (
        "/* 车次数据：车次号 -> 完整经停站序列\n"
        "   生成：python3 tools/convert_trainlist.py（由 12306 车次清单 JSON 转换）\n"
        "   ----------------------------------------------------------------\n"
        "   快照日期(SNAPSHOT)：%s   ← 车次实际运行日期（用户要求的「1028」标注位）\n"
        "   生成日期(GENERATED)：%s   ← 本次转换执行日期\n"
        "   数据来源：%s\n"
        "   stops 是权威经停站（来自 train_detail）；line 仅用于显示线路名。\n"
        "   ----------------------------------------------------------------\n"
        "   注：公开数据源 cr-12306-train-info 最新快照为 2026-06-17（已停更）。\n"
        "       若要生成 2026-10-28 的种子，自行导出 train_list_20261028.json +\n"
        "       train_detail_20261028.json 放入本目录后重跑本脚本，日期会自动识别。\n"
        "   ----------------------------------------------------------------\n"
        "   【后备】12306 官方车站库 station_name.js 按需加载（当前已注释）：\n"
        "   // <script src=\"https://kyfw.12306.cn/otn/resources/js/framework/station_name.js\"></script>\n"
        "   // 加载后 window.station_names 形如 '@站名|拼音|简码|电报码|...'，\n"
        "   // 可用于补全 stations_all.js 缺漏的电报码/拼音；生产仍以 stations_all.js 为准。\n"
        " */\n" % (snapshot_iso or "未知", generated_iso, src_note)
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("window.TRAINS_EXT=")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")

    kb = os.path.getsize(args.out) / 1024
    print("\n输出 %s（%.0f KB，%d 个车次，快照日期 %s）"
          % (args.out, kb, len(ok), snapshot_iso or "未知"))


ARGS = None

if __name__ == "__main__":
    main()
