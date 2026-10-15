"""
铁路足迹 · 车次实时查询代理服务（方案 B 的服务端）

为什么需要它
------------
静态页（index.html）本身完全能 fetch，卡点只在对方服务器发不发 CORS 头：

  * https://search.12306.cn/search/v1/train/search
      → 有 Access-Control-Allow-Origin（原样回显 Origin），浏览器可直连，
        但**只有起终站**，没有经停站。
  * https://kyfw.12306.cn/otn/queryTrainInfo/query
      → **没有任何 access-control-* 头**，浏览器直连必被同源策略拦掉，
        完整经停站只能由服务端取。

于是分工变成：
  本服务 = 静态托管 + 一个极薄代理（把 queryTrainInfo 包一层并补上 CORS），
  静态页 = 一行不用改，照样能部署到 GitHub Pages / 本地 file:// 直开。

接口
----
  GET /api/ping                能力探测，返回 {"ok":true,...}
  GET /api/train?code=G1&date=2026-10-24
  GET /api/trains?codes=G1,K113&date=2026-10-24    批量（车次体检用）

成功返回（经停站齐全）：
  {"ok":true,"code":"G1","from":"北京南","to":"上海虹桥",
   "stops":[...],"trainNo":"24000000G10L","date":"2026-10-24",
   "source":"live","cached":false,"fetchedAt":"..."}

查得到车次但取不到经停站：
  {"ok":false,"code":"G1","reason":"no-stops","from":"北京南","to":"上海虹桥"}

彻底查不到（不在预售期内 / 已裁撤）：
  {"ok":false,"code":"K113","reason":"not-found"}

设计要点
--------
* **只用标准库**，python3 直接跑，不需要 pip install。
* **缓存**：内存 + 磁盘（.traincache/*.json）。正向缓存 24h，负向缓存 15min
  （车次查不到时别把 12306 反复轰）。
* **单飞（single-flight）**：同一个 code+date 并发只放一个上游请求，
  其余等它，避免用户连点查询把频率打满。
* **限速**：全局串行访问上游，两次请求间隔 >= MIN_INTERVAL 秒。
* **日期钳制**：12306 预售期约 15 天，超出窗口传进去只会拿到空结果。
  这里把日期夹到 [今天, 今天+15]，被夹时会回传 adjusted=true。
* **CORS 全开**：Access-Control-Allow-Origin: *，
  这样静态页放在别的域名（GitHub Pages）也能指向本服务。

用法
----
  python3 server.py                     # 127.0.0.1:8787，托管 railway-demo 目录
  python3 server.py --port 9000
  python3 server.py --host 0.0.0.0      # 局域网/容器里给别的设备用
  python3 server.py --no-cache          # 调试：跳过缓存

  然后浏览器打开 http://127.0.0.1:8787/ ，「查经停站」即走实时数据；
  关掉服务退回 file:// 直开，页面自动用本地快照，功能不瞎。
"""

import argparse
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE                       # 静态根目录 = server.py 所在目录（railway-demo）
CACHE_DIR = HERE / ".traincache"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SEARCH_URL = "https://search.12306.cn/search/v1/train/search"
TRAININFO_URL = "https://kyfw.12306.cn/otn/queryTrainInfo/query"

MIN_INTERVAL = 1.0        # 两次上游请求的最小间隔（秒）
UPSTREAM_TIMEOUT = 20     # 单次上游超时
RETRIES = 3               # 上游重试次数
POS_TTL = 24 * 3600       # 正向缓存有效期
NEG_TTL = 15 * 60         # 负向缓存有效期
PRESALE_DAYS = 15         # 12306 预售期（约 15 天）

# ---------------------------------------------------------------- 上游请求

_up_lock = threading.Lock()          # 串行化上游访问
_up_last = [0.0]                     # 上次上游请求时间戳


def _throttle():
    """保证两次上游请求之间至少隔 MIN_INTERVAL 秒。"""
    with _up_lock:
        now = time.monotonic()
        wait = MIN_INTERVAL - (now - _up_last[0])
        if wait > 0:
            time.sleep(wait)
        _up_last[0] = time.monotonic()


def http_get(url, referer=None, timeout=UPSTREAM_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _get_json(url, referer=None):
    """带重试的 GET + JSON 解析。失败返回 None。"""
    for attempt in range(RETRIES):
        _throttle()
        try:
            return json.loads(http_get(url, referer=referer))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if attempt == RETRIES - 1:
                print("    ! 上游失败 %s: %s" % (url[:80], e), flush=True)
                return None
            time.sleep(1.2 * (attempt + 1))
    return None


# ---------------------------------------------------------------- 12306 两步查询

def search_train(code, day):
    """接口 1：车次号 -> 原始记录（含 train_no / from / to）。

    search 是**模糊匹配**（查 Z41 会连带返回 K1130/Z413），
    必须按 station_train_code 精确筛。同一天同一车次可能有多条
    （如 G101 同时有北京朝阳/深圳北开出），取第一条精确匹配。
    """
    url = SEARCH_URL + "?" + urllib.parse.urlencode(
        {"keyword": code, "date": day.strftime("%Y%m%d")})
    data = _get_json(url, referer="https://www.12306.cn/")
    if not data or not data.get("status"):
        return None
    for r in data.get("data") or []:
        if (r.get("station_train_code") or "").upper() == code.upper():
            return r
    return None


def train_stops(train_no, day):
    """接口 2：train_no -> [站名, ...]。注意这里日期要带横线。"""
    url = TRAININFO_URL + "?" + urllib.parse.urlencode({
        "leftTicketDTO.train_no": train_no,
        "leftTicketDTO.train_date": day.strftime("%Y-%m-%d"),
        "rand_code": "",
    })
    data = _get_json(url, referer="https://kyfw.12306.cn/otn/leftTicket/init")
    if not data or not data.get("status"):
        return None
    arr = ((data.get("data") or {}).get("data")) or []
    stops = [s.get("station_name") for s in arr if s.get("station_name")]
    return stops or None


def train_stops_with_fallback(train_no, day):
    """取经停站；预售期边缘（约 +15 天）时刻表还没排全，接口会返回空。

    这时**往前回退几天**取一版代表性的站序——车次停站基本稳定，
    比直接告诉用户「查不到」有用得多。返回 (stops, 实际用的日期)。
    """
    stops = train_stops(train_no, day)
    if stops:
        return stops, day
    for back in (1, 2, 3, 5):
        d2 = day - timedelta(days=back)
        s2 = train_stops(train_no, d2)
        if s2:
            return s2, d2
    return None, day


# ---------------------------------------------------------------- 缓存

class Cache:
    """内存 + 磁盘两级缓存。磁盘缓存让重启服务后仍然命中。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.mem = {}
        self.lock = threading.Lock()
        if enabled:
            try:
                CACHE_DIR.mkdir(exist_ok=True)
            except OSError as e:
                print("! 缓存目录创建失败，改用纯内存缓存: %s" % e, flush=True)
                self.enabled = False

    def _path(self, key):
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return CACHE_DIR / (h + ".json")

    def get(self, key):
        with self.lock:
            hit = self.mem.get(key)
        if hit and hit["expire"] > time.time():
            return hit["value"]
        if hit:
            with self.lock:
                self.mem.pop(key, None)
        if not self.enabled:
            return None
        p = self._path(key)
        try:
            if not p.exists():
                return None
            obj = json.loads(p.read_text(encoding="utf-8"))
            if obj.get("expire", 0) <= time.time():
                p.unlink(missing_ok=True)
                return None
            with self.lock:
                self.mem[key] = obj
            return obj["value"]
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, key, value, ttl):
        obj = {"expire": time.time() + ttl, "value": value, "savedAt": int(time.time())}
        with self.lock:
            self.mem[key] = obj
        if not self.enabled:
            return
        try:
            self._path(key).write_text(
                json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8")
        except OSError:
            pass


CACHE = Cache()
# 单飞表：key -> threading.Event（同一 key 的并发请求共用一次上游查询）
INFLIGHT = {}
INFLIGHT_LOCK = threading.Lock()


# ---------------------------------------------------------------- 日期处理

def clamp_date(day):
    """把日期夹进 12306 预售期 [今天, 今天+15]。返回 (日期, 是否被调整)。"""
    today = date.today()
    if day < today:
        return today, True
    if day > today + timedelta(days=PRESALE_DAYS):
        return today + timedelta(days=PRESALE_DAYS), True
    return day, False


def parse_date(s, default_days=10):
    """解析 yyyy-MM-dd / yyyyMMdd；非法或缺省时用「今天+default_days」。

    为什么默认往后推几天：用户查的多是即将出行的车次，而当天车次
    12306 未必已排好，往后几天取到的时刻表更稳。
    """
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date(), False
        except ValueError:
            continue
    return date.today() + timedelta(days=default_days), False


# ---------------------------------------------------------------- 核心查询

def query_train(code, want_date=None):
    """查一个车次。返回 (payload_dict, http_status)。

    成功：{"ok":true, ...}
    失败：{"ok":false, "reason": ...}
    """
    code = (code or "").strip().upper()
    if not code:
        return {"ok": False, "reason": "missing-code"}, 400

    req_day, _ = parse_date(want_date)
    day, adjusted = clamp_date(req_day)
    key = "train|%s|%s" % (code, day.isoformat())

    hit = CACHE.get(key)
    if hit is not None:
        hit = dict(hit)
        hit["cached"] = True
        return hit, (200 if hit.get("ok") else 404)

    # 单飞：同一 key 已有请求在跑就等它，别重复打 12306
    with INFLIGHT_LOCK:
        ev = INFLIGHT.get(key)
        owner = ev is None
        if owner:
            ev = threading.Event()
            INFLIGHT[key] = ev
    if not owner:
        ev.wait(timeout=UPSTREAM_TIMEOUT * (RETRIES + 2))
        hit = CACHE.get(key)
        if hit is not None:
            hit = dict(hit)
            hit["cached"] = True
            return hit, (200 if hit.get("ok") else 404)
        # 等待超时且缓存里没有，就当成上游故障
        return {"ok": False, "code": code, "reason": "upstream-busy"}, 503

    try:
        payload = _query_train_uncached(code, day, adjusted)
        ttl = POS_TTL if payload.get("ok") else NEG_TTL
        CACHE.put(key, payload, ttl)
        return payload, (200 if payload.get("ok") else 404)
    finally:
        with INFLIGHT_LOCK:
            INFLIGHT.pop(key, None)
        ev.set()


def _query_train_uncached(code, day, adjusted):
    hit = search_train(code, day)
    if not hit:
        return {"ok": False, "code": code, "reason": "not-found", "date": day.isoformat()}

    train_no = hit.get("train_no") or ""
    frm = hit.get("from_station") or ""
    to = hit.get("to_station") or ""
    stops, used_day = train_stops_with_fallback(train_no, day) if train_no else (None, day)

    base = {"code": code, "trainNo": train_no, "date": used_day.isoformat(),
            "adjusted": adjusted or used_day != day, "source": "live", "cached": False,
            "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if used_day != day:
        base["requestedDate"] = day.isoformat()

    if not stops or len(stops) < 2:
        # 车次确实在运营，只是经停站没取到——把起终站也带回去，前端能提示
        base.update({"ok": False, "reason": "no-stops", "from": frm, "to": to})
        return base

    base.update({"ok": True, "from": stops[0], "to": stops[-1], "stops": stops,
                 "fromStation": frm, "toStation": to, "n": len(stops)})
    return base


# ---------------------------------------------------------------- HTTP 层

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "RailwayTrainProxy/1.0"
    protocol_version = "HTTP/1.1"

    # 让默认日志安静一点，只打请求行
    def log_message(self, fmt, *args):
        print("  %s %s" % (self.address_string(), fmt % args), flush=True)

    # ---- 工具 ----
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _file(self, path):
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"ok": False, "reason": "not-found"}, 404)
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 数据文件内容不变，允许强缓存；index.html 不缓存（改版即时生效）
        if path.name == "index.html":
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- 路由 ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if route == "/api/ping":
                return self._json({
                    "ok": True,
                    "service": "railway-train-proxy",
                    "version": 1,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "cache": "on" if CACHE.enabled else "off",
                })

            if route == "/api/train":
                code = (qs.get("code") or [""])[0]
                day = (qs.get("date") or [""])[0]
                payload, status = query_train(code, day)
                return self._json(payload, status)

            if route == "/api/trains":
                codes = (qs.get("codes") or [""])[0]
                day = (qs.get("date") or [""])[0]
                out, worst = {}, 200
                for c in codes.split(","):
                    c = c.strip().upper()
                    if not c:
                        continue
                    payload, status = query_train(c, day)
                    out[c] = payload
                    if status > worst:
                        worst = status
                if not out:
                    return self._json({"ok": False, "reason": "missing-codes"}, 400)
                return self._json({"ok": True, "results": out}, worst)

            # 其余按静态文件处理
            if route in ("/", "/index.html"):
                return self._file(ROOT / "index.html")
            rel = os.path.normpath(parsed.path.lstrip("/"))
            if rel.startswith("..") or os.path.isabs(rel):
                return self._json({"ok": False, "reason": "bad-path"}, 400)
            # .traincache/ 之类的隐藏目录不对外（里面是上游原始响应）
            if any(p.startswith(".") for p in Path(rel).parts):
                return self._json({"ok": False, "reason": "not-found"}, 404)
            target = (ROOT / rel).resolve()
            # 防目录穿越：解析后的真实路径必须还在 ROOT 里
            try:
                target.relative_to(ROOT.resolve())
            except ValueError:
                return self._json({"ok": False, "reason": "bad-path"}, 400)
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                return self._json({"ok": False, "reason": "not-found",
                                   "path": rel}, 404)
            return self._file(target)

        except Exception as e:  # 别让异常把连接挂住
            print("! 处理 %s 出错: %r" % (self.path, e), flush=True)
            try:
                return self._json({"ok": False, "reason": "server-error",
                                   "detail": str(e)}, 500)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="铁路足迹车次查询代理 + 静态托管")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-cache", action="store_true", help="跳过缓存（调试用）")
    args = ap.parse_args()

    global CACHE
    if args.no_cache:
        CACHE = Cache(enabled=False)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = "http://%s:%d/" % (args.host, args.port)
    print("铁路足迹服务已启动：%s" % url)
    print("  静态目录  %s" % ROOT)
    print("  车次查询  %sapi/train?code=G1" % url)
    print("  缓存      %s%s" % ("磁盘+内存 " + str(CACHE_DIR) if CACHE.enabled else "关闭",
                                "" if CACHE.enabled else ""))
    print("Ctrl-C 停止；关掉服务后页面会自动回退到本地快照数据。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
