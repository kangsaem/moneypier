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

# (이름, 설명, 매수 분류, 하락기에만 매수, 매도 방식, 추가 옵션)
#  매도 방식: half = 익절검토·비중축소에 절반·매도에 전부(리포트 규칙) / all = 셋 중 하나면 전부 /
#            hold = 매도·비중축소에 상대강도↓(최근 60일 지수보다 약함)가 겹칠 때만 전부(홀딩형)
#  옵션: stops = [(방식, %), ...] 하나라도 걸리면 매도 — fix_next = 종가가 매수가 -%면 다음 날 종가도 그 아래일 때 매도(내 손절 방식),
#                           fix_now = 종가가 매수가 -%면 그날 종가에 매도, trail = 보유 중 최고 종가 대비 -%면 그날 매도
#        week_exit = 주봉 붕괴(5주<10주 + 종가<10주선)면 다음 날 매도(산 지 5거래일 뒤부터) · extra_sell = 이 분류가 새로 켜지면 전부 매도 · state_sell = 켜져 있는 동안 매도(산 지 5일 뒤부터)
HOLD_BUY = ("매수", "눌림진행")
HOLD_SELL = ("매도·상대강도↓", "비중축소·상대강도↓")
VARIANTS = [
    ("리포트 규칙", "매수·불타기에 사고, 익절검토·비중축소에 절반, 매도에 전부 판다(구버전)", ("매수", "불타기"), False, "half", {}),
    ("리포트 + 손절8%", "리포트 규칙 + 종가가 매수가 -8%면 다음 날 종가도 회복 못 할 때 매도 (기준)", ("매수", "불타기"), False, "half",
     {"stops": [("fix_next", 8)]}),
    ("손절8% + 추세이탈 매도", "기준 + 추세이탈·상대강도↓(5주<10주 + 종가<10주선 + 60일 지수보다 약함)가 새로 켜지면 전부 매도",
     ("매수", "불타기"), False, "half", {"stops": [("fix_next", 8)], "state_sell": ("추세이탈·상대강도↓",)}),
]
# 이전에 시험하고 뺀 변형(2026-10-10 결과 참고): 홀딩형·홀딩+손절·주봉붕괴(구간마다 들쭉날쭉), 매도 신호에 전부, 하락기에만 매수,
# 바닥 2/3·3/3에 매수, 꼭지 매도, 매수 트리거=골든X2, 눌림진행도 매수, 손절8%+고점 대비 -8·10·12% 매도 — HANDOFF.md 참고


def simulate(states, dates, buy_cats, bear_only, sell_mode, opts=None):
    """states: {code: DataFrame(index=날짜, close, cats(set), reg)}. 반환: 잔고 Series, 거래 목록, 현금비중 Series, 기말 보유 수"""
    opts = opts or {}
    stops = list(opts.get("stops") or ([opts["stop"]] if opts.get("stop") else []))
    extra_sell = set(opts.get("extra_sell", ()))
    state_sell = set(opts.get("state_sell", ()))     # 이 분류가 켜져 있는 동안(산 지 5거래일 뒤부터) 매도 — 이미 켜진 상태에서 산 경우도 잡도록
    px = {c: s["close"].reindex(dates).ffill() for c, s in states.items()}
    cats = {c: s["cats"].reindex(dates) for c, s in states.items()}
    regs = {c: s["reg"].reindex(dates) for c, s in states.items()}
    cash, pos = 1.0, {}          # pos[code] = {"sh", "cost", "date", "i", "halved", "real", "entry", "peak", "warn"}
    eq, cash_ratio, trades, orders = [], [], [], []
    prev = {c: set() for c in states}

    def sell(c, q, p, d, i, why=""):
        nonlocal cash
        P = pos[c]
        cash += q * p * (1 - SELL_COST)
        P["real"] += q * p * (1 - SELL_COST)
        P["sh"] -= q
        P["halved"] = True
        if P["sh"] <= 1e-12:
            trades.append({"code": c, "in": P["date"], "out": d, "cost": P["cost"], "back": P["real"],
                           "ret": (P["real"] / P["cost"] - 1) * 100, "days": i - P["i"], "why": why})
            del pos[c]

    for i, d in enumerate(dates):
        # 1) 어제 신호로 만든 주문을 오늘 종가에 체결 (매도 먼저)
        for kind, c in sorted(orders, key=lambda o: o[0] != "sell"):
            p = px[c].iloc[i]
            if pd.isna(p) or p <= 0:
                continue
            if kind in ("sell", "half") and c in pos:
                P = pos[c]
                sell(c, P["sh"] if (kind == "sell" or P["halved"]) else P["sh"] / 2, p, d, i, "신호")
            elif kind == "buy" and c not in pos and len(pos) < SLOTS:
                equity = cash + sum(Q["sh"] * px[k].iloc[i] for k, Q in pos.items() if not pd.isna(px[k].iloc[i]))
                target = equity / SLOTS
                amt = min(target, cash)
                if amt >= target * 0.5:
                    cash -= amt
                    pos[c] = {"sh": amt * (1 - BUY_COST) / p, "cost": amt, "date": d, "i": i, "halved": False, "real": 0.0,
                              "entry": p, "peak": p, "warn": False}
        orders = []
        # 1-2) 손절: 오늘 종가로 판단(오늘 산 종목은 제외). 여러 개면 하나라도 걸리면 매도
        for c in list(pos) if stops else []:
            P, x = pos[c], px[c].iloc[i]
            if pd.isna(x) or P["i"] == i:
                continue
            P["peak"] = max(P["peak"], x)
            why = None
            for how, pct in stops:
                line = (P["peak"] if how == "trail" else P["entry"]) * (1 - pct / 100)
                if how == "fix_next":
                    if x <= line and P["warn"]:          # 어제 종가에 걸렸고 오늘도 회복 못 함 → 오늘 종가에 매도
                        why = "손절"
                    else:
                        P["warn"] = x <= line
                elif x <= line:
                    why = "고점 손절" if how == "trail" else "손절"
            if why:
                sell(c, P["sh"], x, d, i, why)
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
                if sell_mode == "hold":
                    if new & set(HOLD_SELL) or new & extra_sell or (opts.get("week_exit") and "주봉붕괴" in on and i - pos[c]["i"] >= 5):
                        orders.append(("sell", c))
                elif "매도" in new or new & extra_sell or (on & state_sell and i - pos[c]["i"] >= 5):
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


def run(states, names, bench, bench_name, out_path, label, note=""):
    """states가 비어 있지 않을 때 계좌 시뮬레이션을 돌려 out_path(.html)에 저장"""
    dates = sorted(set().union(*[set(s.index) for s in states.values()]))
    dates = pd.DatetimeIndex(dates)
    rows, curves = [], {}
    for nm, desc, buy_cats, bear_only, mode, opts in VARIANTS:
        eq, trades, cash, open_n = simulate(states, dates, buy_cats, bear_only, mode, opts)
        rows.append({"name": nm, "desc": desc, **metrics(eq, trades, cash, open_n), "trades": trades})
        curves[nm] = eq / eq.iloc[0]
    # 비교 대상: 지수 보유, 균등 보유
    benches = bench if isinstance(bench, list) else ([(bench_name, bench)] if bench is not None else [])
    for bname, bser in benches:                    # 지수 보유: 이 대상의 시장 지수가 먼저, 다른 시장 지수도 참고로
        if bser is None or not len(bser):
            continue
        b = bser.reindex(dates, method="ffill").dropna()
        if len(b) > 1:
            b = b / b.iloc[0]
            rows.append({"name": f"{bname} 보유", "desc": "같은 기간 지수를 그냥 들고 있기(배당 제외)", **metrics(b)})
            curves[f"{bname} 보유"] = b
    px = pd.DataFrame({c: s["close"].reindex(dates).ffill() for c, s in states.items()})
    first = px.iloc[0].dropna()
    if len(first):
        ew = (px[first.index] / first).mean(axis=1) * (1 - BUY_COST)
        rows.append({"name": "균등 보유", "desc": f"첫날 {len(first)}종목을 똑같이 나눠 사서 들고 있기", **metrics(ew)})
        curves["균등 보유"] = ew
    write_html(rows, curves, names, out_path, label, dates, note)
    return rows


def _f(v, plus=True, unit="%"):
    if v is None:
        return "-"
    return (f"{v:+.1f}" if plus else f"{v:.0f}") + unit


def write_html(rows, curves, names, out_path, label, dates, note=""):
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
    # 잔고 곡선 데이터(일별, 배수) — 화면에서 고른 구간(1·3·5년) 첫날을 0%로 다시 맞춰 그림
    series = []
    for nm, cv in curves.items():
        cv = cv.reindex(dates).ffill()
        series.append({"name": nm, "bm": nm not in strat, "slot": strat.index(nm) + 1 if nm in strat else 0,
                       "v": [None if pd.isna(x) else round(float(x), 5) for x in cv]})
    years = (dates[-1] - dates[0]).days / 365.25
    wins = [w for w in (1, 3, 5) if w < years - 0.2] + ["전체"]
    data = {"d": [d.strftime("%y.%m.%d") for d in dates], "s": series,
            "w": [{"k": str(w), "label": f"{w}년" if w != "전체" else f"전체({years:.1f}년)",
                   "i": int(dates.searchsorted(dates[-1] - pd.DateOffset(years=w))) if w != "전체" else 0} for w in wins]}
    # 구간별 수익률 표(최근 1·3·5년, 구간 첫날 = 0%)
    head = "".join(f"<th>{E(w['label'])}</th>" for w in data["w"])
    wtr = ""
    for sr in series:
        cells = ""
        for w in data["w"]:
            v = [x for x in sr["v"][w["i"]:] if x is not None]
            r = (v[-1] / v[0] - 1) * 100 if len(v) > 1 else None
            cells += f'<td class="{"up" if r and r > 0 else "dn" if r and r < 0 else ""}">{_f(r)}</td>'
        wtr += f'<tr class="{"bm" if sr["bm"] else ""}"><td class="c">{E(sr["name"])}</td>{cells}</tr>'
    wtable = f'<div class="wrap"><table><tr><th class="c">방식</th>{head}</tr>{wtr}</table></div>'
    tr0 = rows[0].get("trades") or []
    tl = "".join(
        f'<tr><td class="c">{E(names.get(t["code"], t["code"]))}</td><td>{t["in"]:%y.%m.%d}</td><td>{t["out"]:%y.%m.%d}</td>'
        f'<td>{t["days"]}일</td><td class="{"up" if t["ret"] > 0 else "dn"}">{t["ret"]:+.1f}%</td></tr>'
        for t in sorted(tr0, key=lambda t: t["out"], reverse=True))
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{E(label)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --buy:#d92d20; --sell:#1d5fd1;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#6250d6; --s5:#e87ba4; --s6:#008300; --s7:#eda100; --s8:#e34948; --s9:#0f8b8d; --s10:#9c6644; --s11:#5c6bc0; --s12:#7cb342; --bm1:#14181f; --bm2:#9aa0aa; --bm3:#8a5a2b; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --sell:#6ea2ff; --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#9085e9; --s5:#d55181; --s6:#008300; --s7:#c98500; --s8:#e66767; --s9:#2bb5b8; --s10:#c48b62; --s11:#8e99f3; --s12:#9ccc65; --bm1:#eceff4; --bm2:#6b7380; --bm3:#c9965f; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:980px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:18px 0 6px; }} .t, .note {{ color:var(--mut); font-size:.8rem; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c, th.c {{ text-align:left; white-space:normal; }} small {{ color:var(--mut); }}
tr.bm td {{ background:var(--bg); }} td.mut {{ color:var(--mut); text-align:center; }} td.up {{ color:var(--buy); }} td.dn {{ color:var(--sell); }}
.chart {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; position:relative; }}
.lg {{ display:flex; flex-wrap:wrap; gap:4px 14px; font-size:.78rem; color:var(--mut); margin-bottom:4px; }}
.lg i {{ display:inline-block; width:14px; height:2px; vertical-align:middle; margin-right:4px; }}
  .lg .li {{ cursor:pointer; user-select:none; }} .lg .li.off {{ opacity:.35; text-decoration:line-through; }}
.lg i.dash {{ background:none !important; border-top:2px dashed; height:0; }}
svg {{ width:100%; height:auto; display:block; }} .ax {{ fill:var(--mut); font-size:10px; }} .grid {{ stroke:var(--line); }}
.zero {{ stroke:var(--mut); stroke-dasharray:3 3; }} .ln {{ fill:none; stroke-width:2; stroke-linejoin:round; }}
.tip {{ position:absolute; pointer-events:none; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 8px;
  font-size:.75rem; box-shadow:0 2px 8px rgba(0,0,0,.15); display:none; white-space:nowrap; }}
.tip b {{ display:block; margin-bottom:2px; }} .xh {{ stroke:var(--mut); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:6px 0; }} summary {{ cursor:pointer; padding:9px 12px; }}
a {{ color:inherit; }}
.seg {{ display:flex; gap:6px; margin:0 0 6px; }} .seg button {{ font:inherit; font-size:.8rem; padding:4px 12px; border-radius:999px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); cursor:pointer; }} .seg button.on {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }}
</style></head><body>
<h1>{E(label)}</h1>
<div class="t">{dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d} · 계좌 {SLOTS}칸(한 종목 1칸) · 신호 다음 날 종가에 체결 · 비용 매수 {BUY_COST*100:.2f}% / 매도 {SELL_COST*100:.2f}% · 계산 {kst:%Y-%m-%d %H:%M} KST</div>
<div class="t">대상: {E(note or "-")}</div>
<h2>결과</h2>
<div class="wrap"><table>
<tr><th class="c">방식</th><th>총수익</th><th>연평균</th><th>최대 하락</th><th>매매</th><th>승률</th><th>평균 수익</th><th>평균 보유</th><th>현금 비중</th><th>기말 보유</th></tr>
{trs}</table></div>
<p class="note">총수익·연평균·최대 하락은 계좌 전체 기준. 매매·승률·평균 수익·평균 보유는 사서 다 판 종목 기준(기말에 남은 종목은 '기말 보유'로 따로).
현금 비중 = 기간 평균으로 계좌 중 놀고 있던 현금 비율 — 높을수록 신호가 적어 돈이 덜 일했다는 뜻.
한계: 배당 미반영, 체결은 종가 가정. 대상이 '지금 목록'이면 생존 편향(그동안 커진 종목만 담김)이 있어 '균등 보유'가 지수보다 크게 높게 나옴 — 이때는 지수 대신 균등 보유와 비교.</p>
<h2>구간별 수익률</h2>
{wtable}
<p class="note">최근 1·3·5년 구간 첫날을 0%로 본 수익률. 전략은 전체 기간을 이어서 매매한 계좌의 그 구간 성과(구간 첫날 이미 들고 있던 종목 포함). 종목 목록은 이 실행의 시작일 기준 그대로 — 1·3년을 그때의 상위 종목으로 보려면 1년·3년 실행 결과를 볼 것.
코스피·나스닥 보유는 지수 그대로(배당 제외) — 코스피 종목 백테스트에 나스닥은 참고용.</p>
<h2>계좌 잔고 <small id="wl"></small></h2>
<p class="note">범례를 누르면 선을 켜고 끕니다.</p>
<div class="seg" id="seg"></div>
<div class="chart" id="ch"><div class="lg" id="lg"></div><svg id="sv" viewBox="0 0 900 320" role="img" aria-label="계좌 잔고 곡선"></svg><div class="tip" id="tip"></div></div>
<h2>거래 목록 · 리포트 규칙</h2>
<details><summary>다 판 종목 {len(tr0)}건</summary><div class="wrap"><table><tr><th class="c">종목</th><th>산 날</th><th>판 날</th><th>보유</th><th>수익</th></tr>{tl}</table></div></details>
<script>
const D = {json.dumps(data, ensure_ascii=False)};
const W = 900, H = 320, L = 48, R = 12, T = 10, B = 24;
const sv = document.getElementById('sv'), tip = document.getElementById('tip'), lg = document.getElementById('lg'), seg = document.getElementById('seg');
const color = s => s.bm ? (s.name === '균등 보유' ? 'var(--bm2)' : s.name.startsWith('나스닥') ? 'var(--bm3)' : 'var(--bm1)') : 'var(--s' + s.slot + ')';
const SHOW = new Set(D.s.filter(s => !s.bm).map(s => s.name));     // 처음에 보이는 선(범례를 눌러 켜고 끔). 지수·균등 보유는 항상 처음에 보임
D.s.forEach((s, k) => {{ s.off = !s.bm && !SHOW.has(s.name);
  lg.insertAdjacentHTML('beforeend', `<span class="li${{s.off ? ' off' : ''}}" data-k="${{k}}"><i class="${{s.bm ? 'dash' : ''}}" style="background:${{color(s)}};border-color:${{color(s)}}"></i>${{s.name}}</span>`); }});
let cur = null, curW = null;
lg.addEventListener('click', e => {{ const el = e.target.closest('.li'); if (!el) return; const s = D.s[+el.dataset.k];
  s.off = !s.off; el.classList.toggle('off', s.off); draw(curW); }});
function draw(w) {{
  const i0 = w.i, n = D.d.length - i0;
  curW = w;
  const S = D.s.filter(s => !s.off).map(s => {{ const b = s.v.slice(i0).find(v => v !== null);
    return {{ ...s, p: s.v.slice(i0).map(v => v === null || !b ? null : Math.round((v / b - 1) * 1000) / 10) }}; }});
  let lo = 0, hi = 0; S.forEach(s => s.p.forEach(v => {{ if (v !== null) {{ lo = Math.min(lo, v); hi = Math.max(hi, v); }} }}));
  const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * i / Math.max(n - 1, 1), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  let g = '';
  const steps = [2,5,10,20,25,50,100,200,500,1000], stepv = steps.find(s => (hi - lo) / s <= 6) || 2000;
  for (let v = Math.ceil(lo / stepv) * stepv; v <= hi; v += stepv)
    g += `<line class="${{v === 0 ? 'zero' : 'grid'}}" x1="${{L}}" x2="${{W - R}}" y1="${{y(v)}}" y2="${{y(v)}}"/><text class="ax" x="${{L - 6}}" y="${{y(v) + 3}}" text-anchor="end">${{v > 0 ? '+' : ''}}${{v}}%</text>`;
  [0, Math.floor(n / 2), n - 1].forEach(i => g += `<text class="ax" x="${{x(i)}}" y="${{H - 6}}" text-anchor="${{i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}}">${{D.d[i0 + i]}}</text>`);
  S.forEach(s => {{
    let p = '', pen = false;
    s.p.forEach((v, i) => {{ if (v === null) {{ pen = false; return; }} p += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1); pen = true; }});
    g += `<path class="ln" d="${{p}}" stroke="${{color(s)}}" ${{s.bm ? 'stroke-dasharray="5 4"' : ''}}/>`;
  }});
  g += '<line class="xh" id="xh" y1="' + T + '" y2="' + (H - B) + '" style="display:none"/>';
  sv.innerHTML = g;
  document.getElementById('wl').textContent = `${{D.d[i0]}} ~ ${{D.d[D.d.length - 1]}} · 구간 첫날 = 0%`;
  cur = {{ S, n, x, i0 }};
  [...seg.children].forEach(b => b.classList.toggle('on', b.dataset.k === w.k));
}}
D.w.forEach(w => {{ const b = document.createElement('button'); b.textContent = w.label; b.dataset.k = w.k; b.onclick = () => draw(w); seg.appendChild(b); }});
draw(D.w[D.w.length - 1]);
sv.addEventListener('pointermove', e => {{
  const {{ S, n, x, i0 }} = cur, r = sv.getBoundingClientRect(), px = (e.clientX - r.left) * W / r.width;
  const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - R) * (n - 1))));
  const xh = document.getElementById('xh'); xh.setAttribute('x1', x(i)); xh.setAttribute('x2', x(i)); xh.style.display = '';
  tip.innerHTML = '<b>' + D.d[i0 + i] + '</b>' + S.map(s => `<span style="color:${{color(s)}}">●</span> ${{s.name}} ${{s.p[i] === null ? '-' : (s.p[i] > 0 ? '+' : '') + s.p[i].toFixed(1) + '%'}}`).join('<br>');
  tip.style.display = 'block';
  const cw = document.getElementById('ch').clientWidth, tx = (e.clientX - r.left) + 16;
  tip.style.left = (tx + tip.offsetWidth > cw ? tx - tip.offsetWidth - 32 : tx) + 'px'; tip.style.top = '40px';
}});
sv.addEventListener('pointerleave', () => {{ tip.style.display = 'none'; const xh = document.getElementById('xh'); if (xh) xh.style.display = 'none'; }});
</script>
<p class="t"><a href="./">리포트로</a></p>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
