"""야후 국장 일봉 품질 시험(2026-10-14) — 보유·관심 국내 종목, 최근 약 2개월
야후(15:30 정규장 종가로 보임) vs 네이버 일봉(20:00 애프터마켓 반영). 9/14(KRX 애프터마켓 시작) 전후를 나눠 비교.
결과: results/yahoo_check.txt"""
import json, os, re, time, datetime as dt
import requests
import market_report as m

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
KST = dt.timezone(dt.timedelta(hours=9))
AM = "2026-09-14"          # KRX 애프터마켓 시작
FROM = "2026-08-17"        # 비교 시작(애프터마켓 전 약 4주 포함)
out = []
P = lambda *a: (print(*a), out.append(" ".join(str(x) for x in a)))


def yahoo(sym, rng="3mo"):
    j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval=1d", headers=H, timeout=30).json()
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    d = {}
    for t, c, v in zip(r["timestamp"], q["close"], q["volume"]):
        k = dt.datetime.fromtimestamp(t, KST).strftime("%Y-%m-%d")
        d[k] = (c, v)
    return d, r["meta"]


def naver(code):
    st = (dt.datetime.now(KST) - dt.timedelta(days=100)).strftime("%Y%m%d")
    t = requests.get(f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1&startTime={st}"
                     f"&endTime={dt.datetime.now(KST):%Y%m%d}&timeframe=day", headers=H, timeout=30).text
    d = {}
    for mm in re.finditer(r'\["(\d{8})",\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)', t):
        d[f"{mm[1][:4]}-{mm[1][4:6]}-{mm[1][6:]}"] = (float(mm[5]), float(mm[6]))
    return d


tickers = json.loads(os.environ.get("TICKERS_JSON") or "null") or json.load(open("tickers.json", encoding="utf-8"))
tickers = m.resolve_codes(tickers)
kr = [(t["code"].split(".")[0], t["name"]) for t in tickers if t.get("code") and m.is_kr(t["code"])]
seen, rows = set(), []
P(f"실행 {dt.datetime.now(KST):%Y-%m-%d %H:%M} KST · 국내 종목 {len(kr)}개 · 비교 {FROM}~ (애프터마켓 시작 {AM})")
today = dt.datetime.now(KST).strftime("%Y-%m-%d")
for code, name in kr:
    if code in seen:
        continue
    seen.add(code)
    row = {"code": code, "name": name}
    try:
        nv = naver(code)
    except Exception as e:
        nv = {}
        row["nerr"] = str(e)[:80]
    yd, sym, meta = None, None, {}
    for suf in (".KS", ".KQ"):
        try:
            yd, meta = yahoo(code + suf)
            if yd:
                sym = code + suf
                break
        except Exception as e:
            row["yerr"] = str(e)[:80]
    row["sym"] = sym
    if not yd:
        rows.append(row); continue
    nd = sorted(d for d in nv if d >= FROM and d < today)          # 네이버 거래일(오늘 제외 — 오늘 봉은 아직 바뀜)
    ydays = {d for d, (c, v) in yd.items() if c is not None}
    row["missing"] = [d for d in nd if d not in ydays]             # 네이버엔 있는데 야후에 없음
    row["extra"] = sorted(d for d in ydays if d >= FROM and d < today and d not in nv)
    row["null"] = sorted(d for d, (c, v) in yd.items() if c is None and d >= FROM)
    cl = [(d, yd[d][0]) for d in sorted(ydays) if d >= FROM]
    reps, run = [], 1
    for i in range(1, len(cl)):
        if cl[i][1] == cl[i - 1][1]:
            run += 1
            if run == 2:
                reps.append(cl[i - 1][0])
        else:
            run = 1
    row["repeat"] = reps
    diffs = {d: (yd[d][0] / nv[d][0] - 1) * 100 for d in nd if d in ydays and nv[d][0]}
    pre = [abs(v) for d, v in diffs.items() if d < AM]
    post = [abs(v) for d, v in diffs.items() if d >= AM]
    row["pre_n"], row["pre_same"], row["pre_max"] = len(pre), sum(v < 0.01 for v in pre), max(pre) if pre else None
    row["post_n"], row["post_same"], row["post_max"] = len(post), sum(v < 0.01 for v in post), max(post) if post else None
    row["post_mean"] = sum(post) / len(post) if post else None
    row["worst"] = sorted(diffs.items(), key=lambda x: -abs(x[1]))[:2]
    try:
        y10, _ = yahoo(sym, "10y")
        ks = sorted(d for d, (c, v) in y10.items() if c is not None)
        row["y10_n"], row["y10_from"] = len(ks), ks[0] if ks else None
    except Exception as e:
        row["y10_n"] = None
    row["today_y"] = yd.get(today, (None,))[0]
    row["today_n"] = nv.get(today, (None,))[0]
    rows.append(row)
    time.sleep(0.5)

P("\n코드 이름 | 야후 | 9/14 전: 같은 날/비교 · 최대차 | 9/14 후: 같은 날/비교 · 평균차 · 최대차 | 빠짐 | 같은 값 연속 | 10년 일봉 수(시작) | 오늘 야후/네이버")
for r in rows:
    if not r.get("sym"):
        P(f"{r['code']} {r['name']} | 야후 없음 {r.get('yerr','')}")
        continue
    f = lambda v: "-" if v is None else f"{v:.2f}%"
    P(f"{r['code']} {r['name']} | {r['sym']} | {r['pre_same']}/{r['pre_n']} · {f(r['pre_max'])} | {r['post_same']}/{r['post_n']} · {f(r['post_mean'])} · {f(r['post_max'])}"
      f" | {','.join(r['missing']) or '-'}{(' 야후만:' + ','.join(r['extra'])) if r['extra'] else ''}{(' null:' + ','.join(r['null'])) if r['null'] else ''}"
      f" | {','.join(r['repeat']) or '-'} | {r.get('y10_n')}({r.get('y10_from')}) | {r['today_y']}/{r['today_n']}"
      f" | 큰 차이 {[(d, round(v, 2)) for d, v in r['worst']]}")
os.makedirs("results", exist_ok=True)
open("results/yahoo_check.txt", "w", encoding="utf-8").write("\n".join(out))
json.dump(rows, open("results/yahoo_check.json", "w", encoding="utf-8"), ensure_ascii=False, default=str)
