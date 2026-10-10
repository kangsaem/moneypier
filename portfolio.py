"""
계좌 시뮬레이션: 리포트 규칙 그대로 사고팔았을 때의 계좌 잔고를 지수 보유·균등 보유와 비교한다.
backtest.py가 날짜별로 계산한 '그날 켜진 분류'를 받아서 쓴다(신호 재계산 없음).

매매 규칙(기본):
- 신호는 그날 종가로 판단 → 다음 거래일 종가에 체결(그날 종가를 미리 아는 셈이 되지 않도록)
- 매수: 매수·불타기 신호가 새로 켜지면 산다. 계좌를 SLOTS칸으로 나눠 한 종목에 1칸(현재 잔고/SLOTS)씩, 빈칸·현금이 없으면 건너뜀.
  이미 들고 있는 종목은 더 사지 않음.
- 매도: 익절검토·비중축소가 새로 켜지면 절반 매도(이미 절반 판 뒤면 나머지 전부), 매도 신호가 켜지면 전부 매도.
  매도 쪽 신호가 안 뜨면 계속 보유(기간 끝에 남은 종목은 그날 가격으로 평가).
- 비용: 매수 BUY_COST, 매도 SELL_COST(국내 매도세 포함 수준)
"""
import html
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

SLOTS = 10
BUY_COST = 0.0005
SELL_COST = 0.0025
HALF_CATS = ("익절검토", "비중축소")

# (이름, 설명, 매수 분류, 하락기에만 매수, 매도 방식 half|all)
VARIANTS = [
    ("리포트 규칙", "매수·불타기에 사고, 익절검토·비중축소에 절반, 매도에 전부 판다", ("매수", "불타기"), False, "half"),
    ("매도 신호에 전부", "같은 매수, 매도·익절검토·비중축소 중 하나라도 뜨면 전부 판다", ("매수", "불타기"), False, "all"),
    ("눌림진행도 매수", "매수·불타기·눌림진행에 산다(매도는 리포트 규칙)", ("매수", "불타기", "눌림진행"), False, "half"),
    ("하락기에만 매수", "시장이 200일선 아래일 때만 산다(매도는 리포트 규칙)", ("매수", "불타기"), True, "half"),
]


def simulate(states, dates, buy_cats, bear_only, sell_mode):
    """states: {code: DataFrame(index=날짜, close, cats(set), reg)}. 반환: 잔고 Series, 거래 목록, 현금비중 Series"""
    px = {c: s["close"].reindex(dates).ffill() for c, s in states.items()}
    cats = {c: s["cats"].reindex(dates) for c, s in states.items()}
    regs = {c: s["reg"].reindex(dates) for c, s in states.items()}
    cash, pos = 1.0, {}          # pos[code] = {"sh", "cost", "date", "halved", "real"}
    eq, cash_ratio, trades, orders = [], [], [], []
    prev = {c: set() for c in states}
    for i, d in enumerate(dates):
        # 1) 어제 신호로 만든 주문을 오늘 종가에 체결 (매도 먼저)
        for kind, c in sorted(orders, key=lambda o: o[0] != "sell"):
            p = px[c].iloc[i]
            if pd.isna(p) or p <= 0:
                continue
            if kind in ("sell", "half") and c in pos:
                P = pos[c]
                q = P["sh"] if (kind == "sell" or P["halved"]) else P["sh"] / 2
                cash += q * p * (1 - SELL_COST)
                P["real"] += q * p * (1 - SELL_COST)
                P["sh"] -= q
                P["halved"] = True
                if P["sh"] <= 1e-12:
                    trades.append({"code": c, "in": P["date"], "out": d, "cost": P["cost"], "back": P["real"],
                                   "ret": (P["real"] / P["cost"] - 1) * 100, "days": i - P["i"]})
                    del pos[c]
            elif kind == "buy" and c not in pos and len(pos) < SLOTS:
                equity = cash + sum(Q["sh"] * px[k].iloc[i] for k, Q in pos.items() if not pd.isna(px[k].iloc[i]))
                target = equity / SLOTS
                amt = min(target, cash)
                if amt >= target * 0.5:
                    cash -= amt
                    pos[c] = {"sh": amt * (1 - BUY_COST) / p, "cost": amt, "date": d, "i": i, "halved": False, "real": 0.0}
        orders = []
        # 2) 오늘 평가
        val = sum(Q["sh"] * px[k].iloc[i] for k, Q in pos.items() if not pd.isna(px[k].iloc[i]))
        eq.append(cash + val)
        cash_ratio.append(cash / (cash + val) if cash + val > 0 else 1.0)
        # 3) 오늘 신호 → 내일 주문
        for c in states:
            on = cats[c].iloc[i]
            on = on if isinstance(on, (set, frozenset)) else set()
            new = on - prev[c]
            prev[c] = on
            if i == 0:          # 첫날은 이미 켜져 있던 신호라 '새로 켜짐'으로 보지 않음
                continue
            if c in pos:
                if "매도" in new:
                    orders.append(("sell", c))
                elif new & set(HALF_CATS):
                    orders.append(("sell" if sell_mode == "all" else "half", c))
            elif new & set(buy_cats):
                if bear_only and regs[c].iloc[i] != "하락기":
                    continue
                orders.append(("buy", c))
    open_n = len(pos)
    return pd.Series(eq, index=dates), trades, pd.Series(cash_ratio, index=dates), open_n


def metrics(eq, trades=None, cash=None, open_n=None):
    days = len(eq)
    tot = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
    cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (252 / max(days - 1, 1)) - 1) * 100
    mdd = ((eq / eq.cummax()) - 1).min() * 100
    out = {"tot": tot, "cagr": cagr, "mdd": mdd}
    if trades is not None:
        out.update({"n": len(trades), "win": (sum(1 for t in trades if t["ret"] > 0) / len(trades) * 100) if trades else None,
                    "avg": (sum(t["ret"] for t in trades) / len(trades)) if trades else None,
                    "hold": (sum(t["days"] for t in trades) / len(trades)) if trades else None,
                    "cash": float(cash.mean() * 100), "open": open_n})
    return out


def run(states, names, bench, bench_name, out_path, label):
    """states가 비어 있지 않을 때 계좌 시뮬레이션을 돌려 out_path(.html)에 저장"""
    dates = sorted(set().union(*[set(s.index) for s in states.values()]))
    dates = pd.DatetimeIndex(dates)
    rows, curves = [], {}
    for nm, desc, buy_cats, bear_only, mode in VARIANTS:
        eq, trades, cash, open_n = simulate(states, dates, buy_cats, bear_only, mode)
        rows.append({"name": nm, "desc": desc, **metrics(eq, trades, cash, open_n), "trades": trades})
        curves[nm] = eq / eq.iloc[0]
    # 비교 대상: 지수 보유, 균등 보유
    if bench is not None and len(bench):
        b = bench.reindex(dates, method="ffill").dropna()
        if len(b) > 1:
            b = b / b.iloc[0]
            rows.append({"name": f"{bench_name} 보유", "desc": "같은 기간 지수를 그냥 들고 있기", **metrics(b)})
            curves[f"{bench_name} 보유"] = b
    px = pd.DataFrame({c: s["close"].reindex(dates).ffill() for c, s in states.items()})
    first = px.iloc[0].dropna()
    if len(first):
        ew = (px[first.index] / first).mean(axis=1) * (1 - BUY_COST)
        rows.append({"name": "균등 보유", "desc": f"첫날 {len(first)}종목을 똑같이 나눠 사서 들고 있기", **metrics(ew)})
        curves["균등 보유"] = ew
    write_html(rows, curves, names, out_path, label, dates)
    return rows


def _f(v, plus=True, unit="%"):
    if v is None:
        return "-"
    return (f"{v:+.1f}" if plus else f"{v:.0f}") + unit


def write_html(rows, curves, names, out_path, label, dates):
    E = html.escape
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    strat = [r["name"] for r in rows if "n" in r]
    trs = ""
    for r in rows:
        is_s = "n" in r
        trs += (f'<tr class="{"" if is_s else "bm"}"><td class="c"><b>{E(r["name"])}</b><br><small>{E(r["desc"])}</small></td>'
                f'<td>{_f(r["tot"])}</td><td>{_f(r["cagr"])}</td><td>{_f(r["mdd"])}</td>'
                + (f'<td>{r["n"]}</td><td>{_f(r["win"], False)}</td><td>{_f(r["avg"])}</td>'
                   f'<td>{_f(r["hold"], False, "일")}</td><td>{_f(r["cash"], False)}</td><td>{r["open"]}</td>' if is_s
                   else '<td colspan="6" class="mut">-</td>') + "</tr>")
    # 잔고 곡선 데이터 (주 1회로 줄여서)
    step = max(1, len(dates) // 260)
    idx = list(range(0, len(dates), step))
    if idx[-1] != len(dates) - 1:
        idx.append(len(dates) - 1)
    series = []
    for k, (nm, cv) in enumerate(curves.items()):
        cv = cv.reindex(dates).ffill()
        series.append({"name": nm, "bm": nm not in strat, "slot": strat.index(nm) + 1 if nm in strat else 0,
                       "v": [None if pd.isna(cv.iloc[j]) else round(float(cv.iloc[j]) * 100 - 100, 2) for j in idx]})
    data = {"d": [dates[j].strftime("%y.%m.%d") for j in idx], "s": series}
    tr0 = rows[0].get("trades") or []
    tl = "".join(
        f'<tr><td class="c">{E(names.get(t["code"], t["code"]))}</td><td>{t["in"]:%y.%m.%d}</td><td>{t["out"]:%y.%m.%d}</td>'
        f'<td>{t["days"]}일</td><td class="{"up" if t["ret"] > 0 else "dn"}">{t["ret"]:+.1f}%</td></tr>'
        for t in sorted(tr0, key=lambda t: t["out"], reverse=True))
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{E(label)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --buy:#d92d20; --sell:#1d5fd1;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#6250d6; --bm1:#14181f; --bm2:#9aa0aa; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --sell:#6ea2ff; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9; --bm1:#eceff4; --bm2:#6b7380; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:980px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:18px 0 6px; }} .t, .note {{ color:var(--mut); font-size:.8rem; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c, th.c {{ text-align:left; white-space:normal; }} small {{ color:var(--mut); }}
tr.bm td {{ background:var(--bg); }} td.mut {{ color:var(--mut); text-align:center; }} td.up {{ color:var(--buy); }} td.dn {{ color:var(--sell); }}
.chart {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; position:relative; }}
.lg {{ display:flex; flex-wrap:wrap; gap:4px 14px; font-size:.78rem; color:var(--mut); margin-bottom:4px; }}
.lg i {{ display:inline-block; width:14px; height:2px; vertical-align:middle; margin-right:4px; }}
.lg i.dash {{ background:none !important; border-top:2px dashed; height:0; }}
svg {{ width:100%; height:auto; display:block; }} .ax {{ fill:var(--mut); font-size:10px; }} .grid {{ stroke:var(--line); }}
.zero {{ stroke:var(--mut); stroke-dasharray:3 3; }} .ln {{ fill:none; stroke-width:2; stroke-linejoin:round; }}
.tip {{ position:absolute; pointer-events:none; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 8px;
  font-size:.75rem; box-shadow:0 2px 8px rgba(0,0,0,.15); display:none; white-space:nowrap; }}
.tip b {{ display:block; margin-bottom:2px; }} .xh {{ stroke:var(--mut); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:6px 0; }} summary {{ cursor:pointer; padding:9px 12px; }}
a {{ color:inherit; }}
</style></head><body>
<h1>{E(label)}</h1>
<div class="t">{dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d} · 계좌 {SLOTS}칸(한 종목 1칸) · 신호 다음 날 종가에 체결 · 비용 매수 {BUY_COST*100:.2f}% / 매도 {SELL_COST*100:.2f}% · 계산 {kst:%Y-%m-%d %H:%M} KST</div>
<h2>결과</h2>
<div class="wrap"><table>
<tr><th class="c">방식</th><th>총수익</th><th>연평균</th><th>최대 하락</th><th>매매</th><th>승률</th><th>평균 수익</th><th>평균 보유</th><th>현금 비중</th><th>기말 보유</th></tr>
{trs}</table></div>
<p class="note">총수익·연평균·최대 하락은 계좌 전체 기준. 매매·승률·평균 수익·평균 보유는 사서 다 판 종목 기준(기말에 남은 종목은 '기말 보유'로 따로).
현금 비중 = 기간 평균으로 계좌 중 놀고 있던 현금 비율 — 높을수록 신호가 적어 돈이 덜 일했다는 뜻.
한계: 지금 종목 목록 기준(생존 편향), 배당 미반영, 체결은 종가 가정.</p>
<h2>계좌 잔고 (시작 = 0%)</h2>
<div class="chart" id="ch"><div class="lg" id="lg"></div><svg id="sv" viewBox="0 0 900 320" role="img" aria-label="계좌 잔고 곡선"></svg><div class="tip" id="tip"></div></div>
<h2>거래 목록 · 리포트 규칙</h2>
<details><summary>다 판 종목 {len(tr0)}건</summary><div class="wrap"><table><tr><th class="c">종목</th><th>산 날</th><th>판 날</th><th>보유</th><th>수익</th></tr>{tl}</table></div></details>
<script>
const D = {json.dumps(data, ensure_ascii=False)};
const W = 900, H = 320, L = 44, R = 12, T = 10, B = 24;
const sv = document.getElementById('sv'), tip = document.getElementById('tip'), lg = document.getElementById('lg');
const color = s => s.bm ? (s.slot === 0 && s.name === '균등 보유' ? 'var(--bm2)' : 'var(--bm1)') : 'var(--s' + s.slot + ')';
let lo = 0, hi = 0; D.s.forEach(s => s.v.forEach(v => {{ if (v !== null) {{ lo = Math.min(lo, v); hi = Math.max(hi, v); }} }}));
const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
const n = D.d.length, x = i => L + (W - L - R) * i / (n - 1), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
let g = '';
const stepv = [5,10,20,25,50,100,200,500][[5,10,20,25,50,100,200,500].findIndex(s => (hi - lo) / s <= 6)] || 1000;
for (let v = Math.ceil(lo / stepv) * stepv; v <= hi; v += stepv) {{
  g += `<line class="${{v === 0 ? 'zero' : 'grid'}}" x1="${{L}}" x2="${{W - R}}" y1="${{y(v)}}" y2="${{y(v)}}"/><text class="ax" x="${{L - 6}}" y="${{y(v) + 3}}" text-anchor="end">${{v > 0 ? '+' : ''}}${{v}}%</text>`;
}}
[0, Math.floor(n / 2), n - 1].forEach(i => g += `<text class="ax" x="${{x(i)}}" y="${{H - 6}}" text-anchor="${{i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}}">${{D.d[i]}}</text>`);
D.s.forEach(s => {{
  let p = '', pen = false;
  s.v.forEach((v, i) => {{ if (v === null) {{ pen = false; return; }} p += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1); pen = true; }});
  g += `<path class="ln" d="${{p}}" stroke="${{color(s)}}" ${{s.bm ? 'stroke-dasharray="5 4"' : ''}}/>`;
  lg.insertAdjacentHTML('beforeend', `<span><i class="${{s.bm ? 'dash' : ''}}" style="background:${{color(s)}};border-color:${{color(s)}}"></i>${{s.name}}</span>`);
}});
g += '<line class="xh" id="xh" y1="' + T + '" y2="' + (H - B) + '" style="display:none"/>';
sv.innerHTML = g;
const xh = document.getElementById('xh');
sv.addEventListener('pointermove', e => {{
  const r = sv.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
  const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - R) * (n - 1))));
  xh.setAttribute('x1', x(i)); xh.setAttribute('x2', x(i)); xh.style.display = '';
  tip.innerHTML = '<b>' + D.d[i] + '</b>' + D.s.map(s => `<span style="color:${{color(s)}}">●</span> ${{s.name}} ${{s.v[i] === null ? '-' : (s.v[i] > 0 ? '+' : '') + s.v[i].toFixed(1) + '%'}}`).join('<br>');
  tip.style.display = 'block';
  const cw = document.getElementById('ch').clientWidth, tx = (e.clientX - r.left) + 16;
  tip.style.left = (tx + tip.offsetWidth > cw ? tx - tip.offsetWidth - 32 : tx) + 'px'; tip.style.top = '40px';
}});
sv.addEventListener('pointerleave', () => {{ tip.style.display = 'none'; xh.style.display = 'none'; }});
</script>
<p class="t"><a href="./">리포트로</a></p>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
