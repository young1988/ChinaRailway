"""
从 12306 官方接口抓车次与经停站，生成 railway-demo/trains.js。

背景
----
项目原有的车次表（index.html 里的 TRAINS）只有 12 个手写车次，且已严重过时——
实测 Z41 现在是「上海 → 乌鲁木齐」，而表里写的是「兰州 → 乌鲁木齐」。
本脚本把车次数据从「手写常量」变成「可定期刷新的产物」。

两个接口（2026-10 实测）
-----------------------
1) 查车次号 + 起终站（**有 CORS，浏览器可直连**）
   GET https://search.12306.cn/search/v1/train/search
       ?keyword=<车次，如 Z41 或只给字头 Z>
       &date=yyyyMMdd        ← 注意：必须是 yyyyMMdd，传 2026-10-14 会返回空数组
   → {status, data:[{station_train_code, train_no, from_station, to_station, ...}]}
   是**模糊匹配**（查 Z41 会连带返回 K1130/Z413 等），要按 station_train_code 精确筛。
   单次最多 200 条。

2) 查经停站（**无 CORS，只能服务端跑**）
   GET https://kyfw.12306.cn/otn/queryTrainInfo/query
       ?leftTicketDTO.train_no=<上一步的 train_no>
       &leftTicketDTO.train_date=yyyy-MM-dd    ← 这里又是带横线的格式
       &rand_code=
   需要 Referer: https://kyfw.12306.cn/otn/leftTicket/init
   → {status:true, data:{data:[{station_name, arrive_time, start_time, station_no, ...}]}}

限制与坑
--------
* **只能用服务端脚本跑**（接口 2 无 CORS；接口 1 虽然有 CORS，但只给起终站、
  不给经停站，对「选乘降站」功能没用）。
* 日期必须在**预售期内**（约 15 天），否则返回 errorMsg。
* 频率限制较严，脚本默认每个车次间隔 1.2s，别并发轰。
* `search` 是模糊匹配；同一天同一车次可能有多条记录（如 G101 同时有北京朝阳/深圳北开出的），
  取第一条精确匹配即可。
* 站名必须是**项目的车站库里有的名字**，否则前端 `Stations.resolveStation` 解析不出坐标。
  本脚本会校验并把未命中的站名列出来，同时写入 `aliases.js` 建议。

用法
----
  python3 tools/train_fetch.py                      # 刷默认清单
  python3 tools/train_fetch.py --codes G1 G101 Z41  # 指定车次
  python3 tools/train_fetch.py --prefix G            # 拉所有 G 字头（分批）
  python3 tools/train_fetch.py --check              # 只体检不写文件

产出
----
  railway-demo/trains.js  → window.TRAINS_EXT = { "G101": {stops:[...], from, to, ...} }
  与 index.html 里的 TRAINS 合并规则见「三、合并策略」。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SEARCH_URL = "https://search.12306.cn/search/v1/train/search"
TRAININFO_URL = "https://kyfw.12306.cn/otn/queryTrainInfo/query"

# 默认车次清单：覆盖项目已支持的 10 条干线，按字头分组。
# 想要更多就跑 `python3 tools/train_fetch.py --prefix G` 之类。
DEFAULT_PREFIXES = ["G", "D", "C", "Z", "T", "K"]
SEED_CODES = [
    # 京沪 / 京广 / 沪昆 / 京哈 / 陇海 / 京九 / 兰新 / 成昆 / 沿海 / 沪汉蓉
    # 每条干线取几个代表车次；这个清单可以随手加，跑一遍就更新
    "G1", "G3", "G5", "G7", "G9", "G11", "G13", "G15",
    "G101", "G103", "G105", "G107",
    "G65", "G67", "G69", "G71", "G73",
    "G1371", "G1373", "G1375", "G1377", "G1469", "G1651", "G2302",
    "G1", "G3", "G5",
    "Z1", "Z5", "Z9", "Z15", "Z17",
    "K51", "K53", "K111", "K115", "K106", "K1024", "K1020",
    "T109", "T107", "T1", "T169",
    "D3107", "D2206", "D7102", "D3111", "D2202",
    "Z41", "Z105", "Z267", "Z40", "Z42",
    "C1202", "C1206", "G403", "G404",
]

# 项目车站库（用于站名校验）
def load_station_names():
    """返回 (站名集合, 别名映射)。stations_all.js 是单行大字符串，\\n 是字面转义符。"""
    names = set()
    path = os.path.join(ROOT, "stations_all.js")
    if not os.path.exists(path):
        return names, {}
    with open(path, encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r'STATIONS_ALL="([\s\S]*)"\s*;?\s*$', txt)
    if not m:
        return names, {}
    for line in m.group(1).split("\\n"):
        if not line.strip():
            continue
        p = line.split("|")
        if len(p) >= 4:
            names.add(p[1])
    alias = {}
    ap = os.path.join(ROOT, "aliases.js")
    if os.path.exists(ap):
        with open(ap, encoding="utf-8") as f:
            am = re.search(r"=\s*(\{[\s\S]*\})\s*;?\s*$", f.read())
            if am:
                alias = json.loads(am.group(1))
    return names, alias


def http_get(url, referer=None, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def search_train(code, day):
    """接口 1：车次号 -> (train_no, from, to)。模糊匹配里取 station_train_code 精确相等的那条。"""
    ymd = day.strftime("%Y%m%d")
    url = SEARCH_URL + "?" + urllib.parse.urlencode({"keyword": code, "date": ymd})
    for attempt in range(3):
        try:
            data = json.loads(http_get(url, referer="https://www.12306.cn/"))
            break
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            if attempt == 2:
                print(f"    ! 搜索失败 {code}: {e}", file=sys.stderr)
                return None
            time.sleep(1.5 * (attempt + 1))
    if not data.get("status"):
        return None
    rows = data.get("data") or []
    for r in rows:
        if (r.get("station_train_code") or "").upper() == code.upper():
            return r
    return None


def train_stops(train_no, day):
    """接口 2：train_no -> [站名, ...]。注意日期是 yyyy-MM-dd。"""
    dash = day.strftime("%Y-%m-%d")
    url = TRAININFO_URL + "?" + urllib.parse.urlencode({
        "leftTicketDTO.train_no": train_no,
        "leftTicketDTO.train_date": dash,
        "rand_code": "",
    })
    try:
        data = json.loads(http_get(url, referer="https://kyfw.12306.cn/otn/leftTicket/init"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"    ! 经停站失败 {train_no}: {e}", file=sys.stderr)
        return None
    if not data.get("status"):
        return None
    arr = ((data.get("data") or {}).get("data")) or []
    stops = [s.get("station_name") for s in arr if s.get("station_name")]
    return stops or None


def fetch_one(code, day, station_names, alias):
    """抓一个车次，返回 (code, 记录 or None, 报告 dict)。"""
    hit = search_train(code, day)
    if not hit:
        return code, None, {"reason": "搜不到（可能不在预售期内或已裁撤）"}
    tn = hit.get("train_no")
    if not tn:
        return code, None, {"reason": "缺 train_no"}
    stops = train_stops(tn, day)
    if not stops:
        return code, None, {"reason": "拿到车次号但查不到经停站"}
    if len(stops) < 2:
        return code, None, {"reason": "经停站不足 2 个"}

    # 站名校验：前端的 Stations.resolveStation 靠这些名字找坐标
    unknown = [s for s in stops
               if s not in station_names and not (alias.get(s) in station_names)]
    rec = {
        "from": stops[0],
        "to": stops[-1],
        "stops": stops,
        "fetchedAt": day.isoformat(),
    }
    rep = {"reason": None, "unknown": unknown, "n": len(stops)}
    return code, rec, rep


def guess_line(code, rec, line_hint):
    """把车次挂到已有的 10 条干线上（仅用于显示线路名；stops 才是绘制依据）。

    做法：算该车次全程在每条干线经停站上的命中率，取最高分。
    """
    if line_hint and line_hint in KNOWN_LINES:
        return line_hint
    best, best_score = None, 0.0
    stopset = set(rec["stops"])
    for lid, lnames in KNOWN_LINES.items():
        hit = len(stopset & set(lnames))
        if hit < 2:
            continue
        # 用「命中率 × 干线长度占比」打分，避免长途干线通吃
        score = hit / max(1, len(stopset)) * min(1.0, hit / len(lnames))
        if score > best_score:
            best, best_score = lid, score
    return best if best_score >= 0.15 else None


KNOWN_LINES = {}


def load_known_lines():
    """从 index.html 里把 LINES 的 {id: [站名...]} 抠出来，用于自动挂线。"""
    global KNOWN_LINES
    path = os.path.join(ROOT, "index.html")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const LINES = \{([\s\S]*?)\n\};", html)
    if not m:
        return
    block = m.group(1)
    for mm in re.finditer(r"\n  ([a-z0-9_]+): \{(.*?)\n  \},", block, re.S):
        lid, seg = mm.group(1), mm.group(2)
        names = re.findall(r"\['([^']+)'", seg)
        if names:
            KNOWN_LINES[lid] = names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", help="指定车次，如 --codes G1 Z41")
    ap.add_argument("--prefix", action="append", help="按字头批量拉，如 --prefix G")
    ap.add_argument("--days", type=int, default=10, help="用几天后的日期查（须在预售期内，默认 10）")
    ap.add_argument("--sleep", type=float, default=1.2, help="每个车次间隔秒数，默认 1.2")
    ap.add_argument("--limit", type=int, default=0, help="批量模式下最多取多少个车次")
    ap.add_argument("--check", action="store_true", help="只体检不写文件")
    args = ap.parse_args()

    day = date.today() + timedelta(days=args.days)
    print("查询日期 %s（须在预售期内，约 15 天）" % day.isoformat())

    station_names, alias = load_station_names()
    load_known_lines()
    print("车站库 %d 个站名 / 别名 %d 条；预置干线 %d 条\n"
          % (len(station_names), len(alias), len(KNOWN_LINES)))

    codes = list(args.codes or [])
    if not codes:
        codes = list(SEED_CODES)
    # 去重但保持顺序（清单里为了可读性有重复项）
    codes = list(dict.fromkeys(codes))
    if args.prefix:
        for pfx in args.prefix:
            print("按字头 %s 拉取…" % pfx)
            got = search_train(pfx, day)
            rows = (got and "ok") or None
            # 模糊查询字头会返回最多 200 条，这里重新查一次拿全部
            try:
                url = SEARCH_URL + "?" + urllib.parse.urlencode(
                    {"keyword": pfx, "date": day.strftime("%Y%m%d")})
                d = json.loads(http_get(url, referer="https://www.12306.cn/"))
                rows = d.get("data") or []
            except Exception as e:
                print("  ! 失败 %s" % e)
                continue
            seen = set()
            for r in rows:
                c = (r.get("station_train_code") or "").upper()
                if re.fullmatch(re.escape(pfx) + r"\d{1,4}", c) and c not in seen:
                    seen.add(c)
                    codes.append(c)
            print("  找到 %d 个车次" % len(seen))
            time.sleep(args.sleep)

    if args.limit:
        codes = codes[:args.limit]

    ok, bad, unknown_all = {}, {}, {}
    for i, code in enumerate(codes, 1):
        code, rec, rep = fetch_one(code, day, station_names, alias)
        if rec:
            lid = guess_line(code, rec, None)
            if lid:
                rec["line"] = lid
            ok[code] = rec
            flag = "" if not rep.get("unknown") else "  ⚠ %d 站名未收录" % len(rep["unknown"])
            print("  [%3d/%d] ✅ %-7s %2d站  %s → %s%s"
                  % (i, len(codes), code, len(rec["stops"]),
                     rec["from"], rec["to"], flag))
            for u in rep.get("unknown") or []:
                unknown_all.setdefault(u, []).append(code)
        else:
            bad[code] = rep["reason"]
            print("  [%3d/%d] ❌ %-7s %s" % (i, len(codes), code, rep["reason"]))
        time.sleep(args.sleep)

    print("\n===== 汇总 =====")
    print("成功 %d / 失败 %d" % (len(ok), len(bad)))
    if bad:
        print("失败车次: " + "、".join("%s(%s)" % (k, v) for k, v in list(bad.items())[:12]))
    if unknown_all:
        print("\n站名未收录 %d 个（这些站画不出位置，需补进 stations_all.js 或 aliases.js）：" % len(unknown_all))
        for u, cs in sorted(unknown_all.items(), key=lambda x: -len(x[1]))[:20]:
            print("   %-12s 出现于 %s" % (u, "、".join(cs[:3])))

    if args.check or not ok:
        if args.check:
            print("\n--check 模式，未写文件")
        return

    out = {
        "generated": "trains (12306 search + queryTrainInfo)",
        "source": "https://search.12306.cn + https://kyfw.12306.cn/otn/queryTrainInfo",
        "fetchedAt": date.today().isoformat(),
        "queryDate": day.isoformat(),
        "count": len(ok),
        "trains": ok,
    }
    path = os.path.join(ROOT, "trains.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("/* 车次数据：车次号 -> 完整经停站序列\n"
                "   生成：python3 tools/train_fetch.py（服务端跑，queryTrainInfo 无 CORS）\n"
                "   抓取日期：%s　车次数：%d\n"
                "   stops 是权威数据；line 仅用于显示线路名（自动匹配，可为空）。 */\n"
                % (day.isoformat(), len(ok)))
        f.write("window.TRAINS_EXT=")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    kb = os.path.getsize(path) / 1024
    print("\n输出 %s（%.0f KB，%d 个车次）" % (path, kb, len(ok)))
    print("下一步：把 index.html 里的 TRAINS 与 window.TRAINS_EXT 合并（见 train_fetch.py 文档）")


if __name__ == "__main__":
    main()
