"""
바닥·꼭지 단계 추적: 종목 하나를 최근 N거래일 동안 날마다(그날까지의 데이터만으로) 다시 계산해
'바닥 1/3 · 2/3 · 3/3'이 실제 저점에서, '꼭지 1/3 · 2/3 · 3/3'이 실제 고점에서 언제 켜졌는지 차트와 표로 보여 준다.
사용: BT_CODE=417200 BT_NAME=LS머트리얼즈 BT_DAYS=500 python bottom_trace.py  → results/bottom_<코드>.html
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

import market_report as m

CODE = os.environ.get("BT_CODE", "417200").strip()
NAME = os.environ.get("BT_NAME", "").strip() or CODE
DAYS = int(os.environ.get("BT_DAYS", 500))
OUT_DIR = os.environ.get("BT_OUT_DIR", "results")
VERSION = os.environ.get("BT_VERSION", "").strip()   # 결과 파일 이름에 붙여 쌓아 둠(최신본은 bottom_<코드>.html로도 저장)
MEMO = os.environ.get("BT_MEMO", "").strip()


def trace(df, days):
    """날짜별 (날짜, 종가, 단계, 저점 정보)"""
    c = df["Close"].dropna()
    df = df.loc[c.index]
    rows = []
    for t in range(max(260, len(c) - days), len(c)):
        r = m.analyze_df(df.iloc[: t + 1], CODE, NAME, light=True)
        bi, ti = r.get("bottom_info") or {}, r.get("top_info") or {}
        rows.append({"date": c.index[t], "close": float(c.iloc[t]), "stage": int(r.get("bottom") or 0),
                     "low_date": bi.get("low_date"), "low": bi.get("low"), "drop": bi.get("drop"),
                     "top": int(r.get("top") or 0), "high_date": ti.get("high_date"), "high": ti.get("high"), "rise": ti.get("rise")})
    return pd.DataFrame(rows).set_index("date"), c


def episodes(tr, c, kind="bottom"):
    """같은 극점(저점/고점)을 기준으로 묶어서, 각 단계에 처음 들어선 날과 그때 가격(극점 대비), 이후 20·60일 수익률"""
    dcol, vcol, mcol, scol = ("low_date", "low", "drop", "stage") if kind == "bottom" else ("high_date", "high", "rise", "top")
    eps = []
    for ld, g in tr[tr[dcol].notna() & (tr[scol] > 0)].groupby(dcol, sort=True):
        e = {"low_date": ld, "low": float(g[vcol].iloc[0]), "drop": float(g[mcol].iloc[0]), "st": {}}
        for k in (1, 2, 3):
            hit = g[g[scol] >= 1] if k == 1 else g[g[scol] == k]   # 1/3 = 어떤 단계든 처음 켜진 날
            if len(hit):
                d = hit.index[0]
                i = c.index.get_loc(d)
                f = lambda h: (float(c.iloc[i + h]) / float(c.iloc[i]) - 1) * 100 if i + h < len(c) else None
                e["st"][k] = {"date": d, "days": len(c.loc[ld:d]) - 1, "vs_low": (float(c.loc[d]) / e["low"] - 1) * 100,
                              "r20": f(20), "r60": f(60)}
        if e["st"]:
            eps.append(e)
    return eps


EXTRA_WAY = {"클라이맥스꼭지": -1, "RSI하락다이버전스": -1, "저항선실패": -1,
             "RSI상승다이버전스": 1, "지지선반등": 1, "저항선돌파": 1}


def extra_events(df, tr, c):
    """후보 신호 3종이 처음 켜진 날(5거래일 꺼졌다 다시 켜지면 새 신호) + 그 뒤 20·60일 수익률"""
    try:
        ex = m.extra_signals(df.loc[c.index]).loc[tr.index]
    except Exception as e:
        print(f"후보 신호 실패: {e}")
        return []
    out = []
    for nm in EXTRA_WAY:
        last = -10 ** 9
        for k, on in enumerate(ex[nm].values):
            if not on:
                continue
            if k - last > 5:
                d = tr.index[k]
                i = c.index.get_loc(d)
                f = lambda h: (float(c.iloc[i + h]) / float(c.iloc[i]) - 1) * 100 if i + h < len(c) else None
                out.append({"name": nm, "k": k, "date": d, "close": float(c.iloc[i]), "r20": f(20), "r60": f(60)})
            last = k
    return sorted(out, key=lambda e: e["date"])


def extra_rows(evs):
    rows = ""
    for e in evs:
        w = EXTRA_WAY[e["name"]]
        def cell(v):
            if v is None:
                return '<td class="mut">-</td>'
            col = "var(--buy)" if v * w > 0 else "var(--sell)"
            return f'<td style="color:{col}">{v:+.1f}%</td>'
        rows += (f'<tr><td class="c">{e["date"]:%y.%m.%d}</td><td class="c">{"▲" if w > 0 else "▼"} {html.escape(e["name"])}</td>'
                 f'<td>{m.fmt_price(e["close"])}</td>{cell(e["r20"])}{cell(e["r60"])}</tr>')
    return rows or '<tr><td colspan="5" class="mut">기간 안에 켜진 후보 신호 없음</td></tr>'


def fmt(v, unit="%"):
    return "-" if v is None else f"{v:+.1f}{unit}"


def table_rows(eps, kind):
    w, ref = ("저점", "1년 고점 대비") if kind == "bottom" else ("고점", "1년 저점 대비")
    trs = ""
    for e in eps:
        cells = ""
        for k in (1, 2, 3):
            x = e["st"].get(k)
            cells += (f'<td class="g">{x["date"]:%y.%m.%d}<br><small>{w} +{x["days"]}일 · {w} 대비 {fmt(x["vs_low"])}</small></td>'
                      f'<td>{fmt(x["r20"])}</td><td>{fmt(x["r60"])}</td>') if x else '<td class="g mut" colspan="3">-</td>'
        trs += (f'<tr><td class="c">{e["low_date"]:%y.%m.%d}<br><small>{m.fmt_price(e["low"])} · {ref} {e["drop"]:+.0f}%</small></td>{cells}</tr>')
    return trs or f'<tr><td colspan="10" class="mut">기간 안에 {"바닥" if kind == "bottom" else "꼭지"} 단계가 켜진 적 없음</td></tr>'


def write(tr, eps, path, eps_top=(), evs=()):
    E = html.escape
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    data = {"d": [d.strftime("%y.%m.%d") for d in tr.index], "c": [round(v, 4) for v in tr["close"]],
            "s": tr["stage"].tolist(), "t": tr["top"].tolist(),
            "lows": sorted({tr.index.get_loc(e["low_date"]) for e in eps if e["low_date"] in tr.index}),
            "highs": sorted({tr.index.get_loc(e["low_date"]) for e in eps_top if e["low_date"] in tr.index}),
            "ev": [[e["k"], EXTRA_WAY[e["name"]], e["name"]] for e in evs]}
    trs = table_rows(eps, "bottom")
    trs_top = table_rows(eps_top, "top")
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>바닥·꼭지 추적 · {E(NAME)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --buy:#d92d20; --sell:#1d5fd1;
  --b1:rgba(217,45,32,.08); --b2:rgba(217,45,32,.18); --b3:rgba(217,45,32,.32);
  --t1:rgba(29,95,209,.08); --t2:rgba(29,95,209,.18); --t3:rgba(29,95,209,.32); }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --sell:#6ea2ff; --b1:rgba(255,107,94,.10); --b2:rgba(255,107,94,.22); --b3:rgba(255,107,94,.38);
  --t1:rgba(110,162,255,.10); --t2:rgba(110,162,255,.22); --t3:rgba(110,162,255,.38); }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:980px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:18px 0 6px; }} .t, .note {{ color:var(--mut); font-size:.8rem; }}
.chart {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; position:relative; }}
.lg {{ display:flex; flex-wrap:wrap; gap:4px 14px; font-size:.78rem; color:var(--mut); margin-bottom:4px; }}
.lg i {{ display:inline-block; width:12px; height:12px; border-radius:2px; vertical-align:-2px; margin-right:4px; }}
svg {{ width:100%; height:auto; display:block; }} .ax {{ fill:var(--mut); font-size:10px; }} .grid {{ stroke:var(--line); }}
.px {{ fill:none; stroke:var(--fg); stroke-width:1.5; }} .lowm {{ stroke:var(--buy); stroke-dasharray:3 3; }} .lowt {{ fill:var(--buy); font-size:10px; }}
.highm {{ stroke:var(--sell); stroke-dasharray:3 3; }} .hight {{ fill:var(--sell); font-size:10px; }}
.tip {{ position:absolute; pointer-events:none; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 8px; font-size:.75rem;
  box-shadow:0 2px 8px rgba(0,0,0,.15); display:none; white-space:nowrap; }} .xh {{ stroke:var(--mut); }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c {{ text-align:left; }} .g {{ border-left:1px solid var(--line); }} th.g {{ text-align:center; }}
small {{ color:var(--mut); }} .mut {{ color:var(--mut); text-align:center; }} a {{ color:inherit; }}
</style></head><body>
<h1>바닥·꼭지 단계 추적 · {E(NAME)} <small>{E(CODE)}</small></h1>
<div class="t">{tr.index[0]:%Y-%m-%d} ~ {tr.index[-1]:%Y-%m-%d} · 날마다 그날까지의 데이터만으로 다시 계산 · 계산 {kst:%Y-%m-%d %H:%M} KST</div>
<h2>종가와 바닥·꼭지 단계</h2>
<div class="chart" id="ch"><div class="lg"><span><i style="background:var(--b1)"></i>바닥 1/3</span><span><i style="background:var(--b2)"></i>바닥 2/3</span>
<span><i style="background:var(--b3)"></i>바닥 3/3</span><span><i style="background:var(--t1)"></i>꼭지 1/3</span><span><i style="background:var(--t2)"></i>꼭지 2/3</span>
<span><i style="background:var(--t3)"></i>꼭지 3/3</span><span><i style="background:none;border-left:2px dashed var(--buy);border-radius:0;width:0"></i>저점</span>
<span><i style="background:none;border-left:2px dashed var(--sell);border-radius:0;width:0"></i>고점</span>
<span style="color:var(--buy)">▲ 저점 쪽 후보 신호</span><span style="color:var(--sell)">▼ 고점 쪽 후보 신호</span></div>
<svg id="sv" viewBox="0 0 900 300" role="img" aria-label="종가와 바닥 단계"></svg><div class="tip" id="tip"></div></div>
<h2>저점별로 바닥 단계가 켜진 날</h2>
<div class="wrap"><table>
<tr><th rowspan="2">저점</th><th colspan="3" class="g">바닥 1/3 (하락 멈춤)</th><th colspan="3" class="g">바닥 2/3 (바닥 다지기)</th><th colspan="3" class="g">바닥 3/3 (추세 전환)</th></tr>
<tr><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th></tr>
{trs}</table></div>
<h2>고점별로 꼭지 단계가 켜진 날</h2>
<div class="wrap"><table>
<tr><th rowspan="2">고점</th><th colspan="3" class="g">꼭지 1/3 (상승 멈춤)</th><th colspan="3" class="g">꼭지 2/3 (꼭지 다지기)</th><th colspan="3" class="g">꼭지 3/3 (추세 전환)</th></tr>
<tr><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th><th class="g">처음 켜진 날</th><th>20일 뒤</th><th>60일 뒤</th></tr>
{trs_top}</table></div>
<h2>후보 신호가 켜진 날 <small>(백테스트 후보 · 리포트 미반영)</small></h2>
<div class="wrap"><table>
<tr><th class="c">날짜</th><th class="c">신호</th><th>종가</th><th>20일 뒤</th><th>60일 뒤</th></tr>
{extra_rows(evs)}</table></div>
<p class="note">▲ 저점 쪽(RSI상승다이버전스·지지선반등·저항선돌파)은 뒤에 오르면 빨강, ▼ 고점 쪽(클라이맥스꼭지·RSI하락다이버전스·저항선실패)은 뒤에 내리면 빨강 = 맞음.
클라이맥스꼭지 = 60일 신고가 고점 ±3일에 거래량 {m.CLX_VOL}배 이상 + 고점 뒤 5~10일 신고가 없음 + 최근 5일 거래량 평균 아래.
RSI 다이버전스 = 최근 5일 저점(고점)이 10~60일 전 것보다 낮은데(높은데) RSI는 {m.DIV_RSI} 이상 높음(낮음), 앞쪽 RSI는 과매도(과열) + 오늘 반대로 움직임.
지지선·저항선 = 20~250일 전 앞뒤 {m.SR_SWING}일 중 최저·최고 종가, ±{m.SR_BAND:.0f}%를 근처로 봄.</p>
<p class="note">'저점' = 그날 판정에 쓰인 첫 저점(최근 1년 고점 이후의 최저 종가, 그 고점 대비 -25% 이하, 저점 뒤 120거래일까지만 봄). 저점 +n일 = 저점 뒤 몇 거래일 만에 켜졌는지,
저점 대비 = 그날 종가가 저점보다 몇 % 위였는지(바닥에 얼마나 가깝게 잡았는지). 20·60일 뒤 = 그날 샀다면의 수익률.
더 낮은 저점이 나오거나 새 1년 고점 뒤 다시 급락하면 저점이 바뀌어 새 줄이 생김.</p>
<p class="t"><a href="./">리포트로</a> · <a href="backtests.html">지난 실행과 비교 →</a></p>
<script>
const D = {json.dumps(data, ensure_ascii=False)};
const W = 900, H = 300, L = 56, R = 10, T = 8, B = 22, n = D.c.length;
const lo = Math.min(...D.c) * 0.97, hi = Math.max(...D.c) * 1.03;
const x = i => L + (W - L - R) * i / (n - 1), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
const sv = document.getElementById('sv'), tip = document.getElementById('tip');
let g = '';
for (let k = 0; k <= 4; k++) {{ const v = lo + (hi - lo) * k / 4;
  g += `<line class="grid" x1="${{L}}" x2="${{W - R}}" y1="${{y(v)}}" y2="${{y(v)}}"/><text class="ax" x="${{L - 6}}" y="${{y(v) + 3}}" text-anchor="end">${{v >= 1000 ? Math.round(v).toLocaleString() : v.toFixed(2)}}</text>`; }}
const bw = (W - L - R) / (n - 1);
D.s.forEach((s, i) => {{ if (s) g += `<rect x="${{x(i) - bw / 2}}" y="${{T}}" width="${{bw + 0.5}}" height="${{H - T - B}}" fill="var(--b${{s}})"/>`; }});
D.t.forEach((s, i) => {{ if (s) g += `<rect x="${{x(i) - bw / 2}}" y="${{T}}" width="${{bw + 0.5}}" height="${{H - T - B}}" fill="var(--t${{s}})"/>`; }});
D.highs.forEach(i => g += `<line class="highm" x1="${{x(i)}}" x2="${{x(i)}}" y1="${{T}}" y2="${{H - B}}"/><text class="hight" x="${{x(i) + 3}}" y="${{T + 12}}">고점 ${{D.d[i]}}</text>`);
D.lows.forEach(i => g += `<line class="lowm" x1="${{x(i)}}" x2="${{x(i)}}" y1="${{T}}" y2="${{H - B}}"/><text class="lowt" x="${{x(i) + 3}}" y="${{H - B - 4}}">저점 ${{D.d[i]}}</text>`);
D.ev.forEach(([i, w, nm]) => {{ const yy = y(D.c[i]) + (w > 0 ? 12 : -6);
  g += `<text x="${{x(i)}}" y="${{yy}}" text-anchor="middle" font-size="10" fill="var(--${{w > 0 ? 'buy' : 'sell'}})"><title>${{D.d[i]}} ${{nm}}</title>${{w > 0 ? '▲' : '▼'}}</text>`; }});
g += `<path class="px" d="${{D.c.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1)).join('')}}"/>`;
[0, Math.floor(n / 2), n - 1].forEach(i => g += `<text class="ax" x="${{x(i)}}" y="${{H - 6}}" text-anchor="${{i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}}">${{D.d[i]}}</text>`);
g += `<line class="xh" id="xh" y1="${{T}}" y2="${{H - B}}" style="display:none"/>`;
sv.innerHTML = g;
const xh = document.getElementById('xh');
sv.addEventListener('pointermove', e => {{
  const r = sv.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
  const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - R) * (n - 1))));
  xh.setAttribute('x1', x(i)); xh.setAttribute('x2', x(i)); xh.style.display = '';
  const evs = D.ev.filter(e => e[0] === i).map(e => (e[1] > 0 ? '▲ ' : '▼ ') + e[2]).join('<br>');
  tip.innerHTML = `<b>${{D.d[i]}}</b><br>종가 ${{D.c[i] >= 1000 ? Math.round(D.c[i]).toLocaleString() : D.c[i].toFixed(2)}}<br>${{D.s[i] ? '바닥 ' + D.s[i] + '/3' : D.t[i] ? '꼭지 ' + D.t[i] + '/3' : '단계 없음'}}${{evs ? '<br>' + evs : ''}}`;
  tip.style.display = 'block';
  const cw = document.getElementById('ch').clientWidth, tx = (e.clientX - r.left) + 14;
  tip.style.left = (tx + tip.offsetWidth > cw ? tx - tip.offsetWidth - 28 : tx) + 'px'; tip.style.top = '36px';
}});
sv.addEventListener('pointerleave', () => {{ tip.style.display = 'none'; xh.style.display = 'none'; }});
</script>
</body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    df = m.load_prices(CODE)
    tr, c = trace(df, DAYS)
    eps, eps_top = episodes(tr, c, "bottom"), episodes(tr, c, "top")
    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"bottom_{CODE}" + (f"_{VERSION}" if VERSION else "")
    path = os.path.join(OUT_DIR, name + ".html")
    evs = extra_events(df, tr, c)
    write(tr, eps, path, eps_top, evs)
    if VERSION:
        import shutil
        shutil.copyfile(path, os.path.join(OUT_DIR, f"bottom_{CODE}.html"))
    def brief(es):
        out = []
        for e in es:
            st = {str(k): {"date": f"{v['date']:%Y-%m-%d}", "days": v["days"], "vs": round(v["vs_low"], 1),
                           "r20": None if v["r20"] is None else round(v["r20"], 1)} for k, v in e["st"].items()}
            out.append({"ext_date": f"{e['low_date']:%Y-%m-%d}", "ext": e["low"], "st": st})
        return out
    summ = {"kind": "trace", "code": CODE, "label": f"바닥·꼭지 추적 · {NAME}", "version": VERSION or "latest", "memo": MEMO,
            "commit": os.environ.get("GITHUB_SHA", "")[:7], "files": {"html": name + ".html"},
            "time": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
            "bottom": brief(eps), "top": brief(eps_top),
            "extra": [{"name": e["name"], "date": f"{e['date']:%Y-%m-%d}", "r20": None if e["r20"] is None else round(e["r20"], 1)} for e in evs]}
    with open(os.path.join(OUT_DIR, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1)
    print(f"{path} 생성 완료 (저점 {len(eps)}개, 고점 {len(eps_top)}개)")


if __name__ == "__main__":
    main()
