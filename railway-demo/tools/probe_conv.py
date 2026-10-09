"""试探普速数据量级：拿河南一个省估算全国体量。"""
import sys, time, json, collections, math
sys.path.insert(0, '.')
import fetch_highspeed as F


def km(g):
    s = 0.0
    for i in range(len(g) - 1):
        a, b = g[i], g[i + 1]
        R = 6371
        t = math.pi / 180
        h = (math.sin((b['lat'] - a['lat']) * t / 2) ** 2
             + math.cos(a['lat'] * t) * math.cos(b['lat'] * t)
             * math.sin((b['lon'] - a['lon']) * t / 2) ** 2)
        s += 2 * R * math.asin(math.sqrt(h))
    return s


def probe(prov):
    bbox = F.PROVINCES[prov]
    print(f'{prov} bbox={bbox}', flush=True)
    q = ('[out:json][timeout:180][bbox:%s,%s,%s,%s];'
         'way["railway"="rail"]["usage"="main"]["highspeed"!="yes"];out tags geom;' % bbox)
    t0 = time.time()
    d = F.fetch(q)
    dt = time.time() - t0
    if not d:
        print('查询失败', flush=True)
        return
    els = [e for e in d.get('elements', []) if e.get('type') == 'way' and e.get('geometry')]
    tot = pts = 0
    agg = collections.Counter()
    for e in els:
        g = e['geometry']
        k = km(g)
        tot += k
        pts += len(g)
        agg[e.get('tags', {}).get('name') or '(无名)'] += k
    payload = len(json.dumps(d, ensure_ascii=False)) / 1024
    print(f'耗时 {dt:.0f}s  ways={len(els)}  总里程 {tot:.0f}km  原始点 {pts}  '
          f'payload {payload:.0f}KB  线路名 {len(agg)} 个', flush=True)
    print('=== TOP 15 ===', flush=True)
    for n, v in agg.most_common(15):
        print(f'  {n:<16}{v:>6.0f} km', flush=True)
    print(f'>>> 估算全国(按里程外推 {34}省均值): 原始点约 {pts*34}  原始 payload 约 {payload*34/1024:.1f} MB',
          flush=True)


if __name__ == '__main__':
    probe(sys.argv[1] if len(sys.argv) > 1 else '河南')
