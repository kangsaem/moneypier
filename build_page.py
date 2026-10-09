"""
tickers.json(또는 환경변수 TICKERS_JSON)의 종목들로 리포트를 만들어
1) docs/index.html 저장  2) 텔레그램 토큰이 있으면 메시지로도 전송
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone

import requests

import market_report as m

MINI_DAYS = m.CHART_DAYS   # 시그널 카드 소형 차트 기간. 기간은 market_report.py의 CHART_DAYS에서 바꾼다
render_errors = []


def guard(label, fn, *args, default=""):
    """한 부분이 실패해도 리포트 전체는 계속 만든다. 실패 내용은 페이지 하단에 표시"""
    try:
        return fn(*args)
    except Exception as e:
        render_errors.append(f"{label} 실패: {type(e).__name__}: {e}")
        return default



def load_tickers():
    raw = os.environ.get("TICKERS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    with open("tickers.json", encoding="utf-8") as f:
        return json.load(f)


def resolve_codes(items):
    """코드 없이 이름만 있는 종목(금액만 입력한 경우)은 KRX 종목/ETF 목록에서 이름으로 찾는다"""
    if all(t.get("code") for t in items):
        return items
    names = {}
    try:
        import FinanceDataReader as fdr
        for market, col in (("KRX", "Code"), ("ETF/KR", "Symbol")):
            try:
                lst = fdr.StockListing(market)
                names.update(dict(zip(lst["Name"], lst[col])))
            except Exception as e:
                print(f"[{market} 목록] 실패: {e}")
    except Exception as e:
        print(f"[종목 목록] 실패: {e}")
    for t in items:
        if not t.get("code"):
            t["code"] = names.get(t["name"], "")
    return items


tickers = resolve_codes(load_tickers())
unresolved = [t["name"] for t in tickers if not t.get("code")]
tickers = [t for t in tickers if t.get("code")]

results, failed = [], []
for t in tickers:
    try:
        results.append(m.analyze(t["code"], t["name"]))
    except Exception as e:
        failed.append(f"{t['name']} ({t['code']}): {type(e).__name__}: {e}")

snap = guard("시장 지표 수집", m.market_snapshot,
             default={"ks": None, "vix": None, "y10": None, "y30": None, "err": ["시장 지표 수집 실패"]})
msig = guard("위험·바닥 지표 계산", m.market_signals, snap, results,
             default={"risk": {"items": [], "score": 0, "total": 0, "label": "계산 실패", "cls": "lv0"}, "bottom": {"items": [], "score": 0, "total": 0, "label": "계산 실패", "cls": "lv0"}})
head = guard("시장 지표 문구", m.market_text, snap)
sigs = guard("시그널 문구", m.signals_text, results, msig)

notes = ""
if failed:
    notes += "\n\n[시세 조회 실패 종목]\n" + "\n".join(failed)
if unresolved:
    notes += "\n\n[코드를 찾지 못해 제외된 종목] " + ", ".join(unresolved)

repo = os.environ.get("GITHUB_REPOSITORY", "")
link = f"\n\n전체 리포트: https://{repo.split('/')[0]}.github.io/{repo.split('/')[1]}/" if "/" in repo else ""
short_report = head + "\n\n" + sigs + notes + link          # 텔레그램용(시그널 중심, 세부 제외)

kst = datetime.now(timezone.utc) + timedelta(hours=9)
stamp = f"{kst:%Y-%m-%d %H:%M} KST"
E = html.escape


def chip(txt, cls):
    return f'<span class="chip {cls}">{E(txt)}</span>'


def card(label, value, sub="", cls=""):
    return f'<div class="card {cls}"><div class="cl">{E(label)}</div><div class="cv">{E(value)}</div><div class="cs">{E(sub)}</div></div>'


def updown(x):
    return "up" if x > 0 else "dn" if x < 0 else ""


cards = []
ks = snap.get("ks")
if ks:
    cards.append(card("코스피", f"{ks['close']:,.0f}", f"{ks['chg']:+.2f}% · 이격도 {ks['disp']:.0f} · RSI {ks['rsi']:.0f}", updown(ks["chg"])))
for key, nm, unit in (("vix", "VIX", ""), ("y10", "미국채 10년", "%"), ("y30", "미국채 30년", "%")):
    v = snap.get(key)
    if v:
        cards.append(card(nm, f"{v['last']:.2f}{unit}", f"전일 대비 {v['chg']:+.2f}", updown(v["chg"])))
for e in snap.get("err", []):
    cards.append(card("실패", e, "", "bad"))


def gauge(key, title):
    g = msig[key]
    rows = "".join(
        f'<li class="{"on" if st else "na" if st is None else "off"}"><span class="dot"></span>'
        f'<span class="t">{E(lb)}</span><b>{E(val)}</b></li>' for lb, val, st in g["items"])
    pct = int(100 * g["score"] / g["total"]) if g["total"] else 0
    return (f'<div class="gauge {key} {g["cls"]}"><div class="gh"><span>{title}</span>'
            f'<b>{g["score"]}/{g["total"]} · {E(g["label"])}</b></div>'
            f'<div class="bar"><i style="width:{pct}%"></i></div><ul>{rows}</ul></div>')



def disp_cls(v):
    return ("h2" if v >= 120 else "h1" if v >= 110 else "n" if v > 95 else "l1" if v > 90 else "l2")


def _metrics_html(r, side, lv):
    """종목 한 줄 요약 태그. 조건에 걸린 태그는 테두리, 트리거에 걸린 태그는 채움으로 강조"""
    keys = r["sig"][side + "_k"]

    def tag(key, txt, color, title=""):
        mark = {"c": " cond", "t": " trig"}.get(keys.get(key), "")
        ttl = f' title="{E(title)}"' if title else ""
        return f'<span class="mt {color}{mark}"{ttl}>{E(txt)}</span>'

    out = []
    d = r["disp"]
    if d:
        v = m.disp_view(d)
        if v["side"] == "high":      # 높은 쪽 = 빨강, 과거 최대 대비 %
            cls = "h2" if v["strong"] else "h1"
        else:                        # 낮은 쪽 = 하늘색, 과거 최소 대비 %
            cls = "l2" if v["strong"] else "l1"
        txt = f"50일 이격 {v['pct']:.0f}% ({m._n(d['min'])}/{m._n(d['max'])})"
        out.append(tag("disp", txt, cls, f"현재 50일 이격도 {d['cur']:.1f}"))
    trig = r["sig"][side + "_t"]
    ago = r["day"]["cross"]["ago"] if r["day"]["ok"] and r["day"]["cross"] else 0
    day_note = ""
    for t_ in trig:
        if "임박" in t_:
            day_note = " · 골든 임박"
        elif "골든" in t_:
            day_note = f" · 골든 {ago}일전"
        elif "데드" in t_:
            day_note = f" · 데드 {ago}일전"
    rsi_note = "".join(" ↗반등" if "반등" in t_ else " ↘꺾임" if "꺾임" in t_ else "" for t_ in trig)
    for lb, k in (("일", "day"), ("주", "week"), ("월", "month")):
        x = r[k]
        label = (lb + (" 정" if x["above"] else " 역") if x["ok"] else lb + " -") + (day_note if k == "day" else "")
        out.append(tag("day" if k == "day" else "", label,
                       "up" if x["ok"] and x["above"] else "dn" if x["ok"] else "n"))
    g = (r["close"] / r["ma10"] - 1) * 100
    m20 = r["chart"]["ma20"][-1] if r.get("chart") else None
    if m20:
        g20 = (r["close"] / m20 - 1) * 100
        out.append(tag("ma20", f"20일 {abs(g20):.1f}% {'▲' if g20 >= 0 else '▼'}", "up" if g20 >= 0 else "dn",
                       "눌림 기준선(20일선) 대비 종가 위치"))
    out.append(tag("", f"10일 {abs(g):.1f}% {'▲' if g >= 0 else '▼'}" + (f" · 이탈선 {m.fmt_price(r['ma10'])}" if lv == "과열" else ""),
                   "up" if g >= 0 else "dn"))
    for lab, k in (("10주", "w10"), ("10월", "m10")):
        mv = r["chart"][k][-1] if r.get("chart") else None
        if mv:
            gp = (r["close"] / mv - 1) * 100
            out.append(tag("", f"{lab} {abs(gp):.0f}% {'▲' if gp >= 0 else '▼'}", "up" if gp >= 0 else "dn",
                           f"종가가 {lab}선보다 {abs(gp):.1f}% {'위' if gp >= 0 else '아래 (위쪽 저항 가능)'}"))
    out.append(tag("rsi", f"RSI {r['rsi']:.0f}{rsi_note}", "hot" if r["rsi"] >= 70 else "cold" if r["rsi"] <= 30 else "n"))
    out.append(tag("ret5", f"5일 {r['ret5']:+.1f}%", "up" if r["ret5"] > 0 else "dn"))
    out.append(tag("high", f"52주 고점 {r['from_high']:+.0f}%", "n"))
    return '<div class="mrow">' + "".join(out) + "</div>"


def _path(vals, x, y):
    d, pen = [], False
    for i, v in enumerate(vals):
        if v is None:
            pen = False
            continue
        d.append(("L" if pen else "M") + f"{x(i):.1f},{y(v):.1f}")
        pen = True
    return "".join(d)


def _svg_chart(ch, mini=False):
    """인라인 SVG: 이평선(5·20일, 5·10주, 5·10월) + 오늘 종가 점 (+ RSI 패널). 종가 선은 그리지 않는다"""
    if not ch or len(ch["close"]) < 5:
        return ""
    sl = slice(-MINI_DAYS, None) if mini else slice(None)
    g = lambda k: ch[k][sl]
    keys = ("ma5", "ma20", "w5", "w10", "m5", "m10")
    close = g("close")
    n = len(close)
    W = 320 if mini else 640
    PH = 64 if mini else 170
    RH = 0 if (mini or not ch.get("rsi")) else 54
    gap = 10 if RH else 0
    pl, pr = 4, (4 if mini else 50)
    pt, pb = (4 if mini else 18), (0 if mini else 16)
    H = pt + PH + gap + RH + pb
    vals = [v for k in keys for v in g(k) if v is not None] + [close[-1]]   # 오늘 종가 점이 범위 안에 들도록
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    m_ = (hi - lo) * 0.04
    lo, hi = lo - m_, hi + m_
    x = lambda i: pl + (W - pl - pr) * i / (n - 1)
    y = lambda v: pt + PH * (1 - (v - lo) / (hi - lo))
    o = [f'<svg class="chart{" mini" if mini else ""}" viewBox="0 0 {W} {H}" role="img" aria-label="가격 차트">']
    o.append(f'<rect class="ch-bg" x="0" y="{pt}" width="{W - pr}" height="{PH}"/>')
    for k in keys[::-1]:
        o.append(f'<path class="ch-{k}" d="{_path(g(k), x, y)}"/>')
    ly = y(close[-1])
    o.append(f'<circle class="ch-dot" cx="{x(n - 1):.1f}" cy="{ly:.1f}" r="{2.5 if mini else 3}"/>')
    if not mini:
        for v, ypos in ((hi - m_, y(hi - m_)), (lo + m_, y(lo + m_))):
            if abs(ypos - ly) < 10:      # 현재가 라벨과 겹치면 최고·최저 라벨은 생략
                continue
            o.append(f'<text class="ch-t" x="{W - pr + 4}" y="{ypos + 3:.1f}">{m.fmt_price(v)}</text>')
        o.append(f'<text class="ch-last" x="{W - pr + 4}" y="{ly + 3:.1f}">{m.fmt_price(close[-1])}</text>')
        for i, (lab, cls) in enumerate((("오늘 종가", "close"), ("5일", "ma5"), ("20일", "ma20"), ("5주", "w5"), ("10주", "w10"), ("5월", "m5"), ("10월", "m10"))):
            o.append(f'<text class="ch-lg ch-l{cls}" x="{pl + 2 + i * 50 + (14 if i else 0)}" y="11">● {lab}</text>')
        o.append(f'<text class="ch-t" x="{pl}" y="{H - 3}">{ch["dates"][sl][0]}</text>')
        o.append(f'<text class="ch-t" x="{W - pr}" y="{H - 3}" text-anchor="end">{ch["dates"][sl][-1]}</text>')
        if RH:
            top = pt + PH + gap
            y2 = lambda v: top + RH * (1 - v / 100)
            o.append(f'<rect class="ch-bg" x="0" y="{top}" width="{W - pr}" height="{RH}"/>')
            rv = g("rsi")
            last = next((v for v in reversed(rv) if v is not None), None)
            for lv in (30, 70):
                o.append(f'<line class="ch-grid" x1="0" x2="{W - pr}" y1="{y2(lv):.1f}" y2="{y2(lv):.1f}"/>')
                if last is None or abs(y2(lv) - y2(last)) > 9:      # RSI 값 라벨과 겹치면 기준선 숫자는 생략
                    o.append(f'<text class="ch-t" x="{W - pr + 4}" y="{y2(lv) + 3:.1f}">{lv}</text>')
            o.append(f'<path class="ch-rsi" d="{_path(rv, x, y2)}"/>')
            if last is not None:
                o.append(f'<text class="ch-last" x="{W - pr + 4}" y="{y2(last) + 3:.1f}">RSI {last:.0f}</text>')
    o.append("</svg>")
    return "".join(o)


def svg_chart(ch, mini=False):
    try:
        return _svg_chart(ch, mini)
    except Exception as e:
        render_errors.append(f"차트 생성 실패: {type(e).__name__}: {e}")
        return ""


def metrics_html(r, side, lv):
    try:
        return _metrics_html(r, side, lv)
    except Exception as e:
        render_errors.append(f"{r.get('name')} 지표 태그 실패: {type(e).__name__}: {e}")
        return ""


def sig_block(head_txt, side, lv, cls):
    hit = [r for r in results if r["sig"][side] == lv]
    items = ""
    for r in hit:
        sg = r["sig"]
        items += (f'<div class="sig"><div class="sh"><b>{E(r["name"])}</b><span>{E(r["code"])}</span>'
                  f'</div>{metrics_html(r, side, lv)}{svg_chart(r["chart"], mini=True)}</div>')
    body = items or '<div class="none">해당 종목 없음</div>'
    return f'<div class="sg {cls}"><h3>{head_txt} <small>{len(hit)}</small></h3>{body}</div>'


sig_html = (sig_block("불타기 후보 <small>(추세 유지 · 과열 아님 · 20일선 눌림 후 반등)</small>", "add", "후보", "add")
            + sig_block("매수 타점", "buy", "타점", "buy strong")
            + sig_block("저평가 · 반등 대기 <small>(방향 확인 전)</small>", "buy", "관심", "buy")
            + sig_block("하락 진행 중 <small>(매수 보류)</small>", "buy", "보류", "hold")
            + sig_block("매도 검토 <small>(과열 + 꺾임 확인)</small>", "sell", "타점", "sell strong")
            + sig_block("일부 익절 검토 <small>(과열 · 추세는 유지)</small>", "sell", "과열", "hot")
            + sig_block("비중 축소 검토 <small>(과열 + 일·주봉 중 역배열)</small>", "sell", "관심", "sell"))

screen_html = ""
for h, n, lines in guard("스크리닝", m.summary_sections, results, default=[]):
    body = E("\n".join(lines)) if n else "해당 종목 없음"
    screen_html += f'<details><summary>{E(h)} <small>{n}</small></summary><pre>{body}</pre></details>'

detail_html = ""
for r in results:
    fl = guard(f"{r['name']} 태그", m.flags, r, default=[])
    chips = "".join(chip(f, "buy" if f.startswith("매수") else "sell" if f.startswith("매도") else "flag") for f in fl)
    rc = "hot" if r["rsi"] >= 70 else "cold" if r["rsi"] <= 30 else ""
    detail_html += (f'<details><summary><b>{E(r["name"])}</b> <span class="code">{E(r["code"])}</span> '
                    f'<span class="rsi {rc}">RSI {r["rsi"]:.0f}</span> {chips}</summary>'
                    f'<div class="chwrap">{svg_chart(r["chart"])}</div><pre>{E(guard(r['name'] + " 세부내용", m.detail_text, r))}</pre></details>')

if render_errors:
    notes += "\n\n[화면 생성 중 오류]\n" + "\n".join(sorted(set(render_errors)))
extra = f'<pre class="warn">{E(notes.strip())}</pre>' if notes.strip() else ""

page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>장 마감 리포트</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb;
  --buy:#d92d20; --buybg:#fdecea; --sell:#1d5fd1; --sellbg:#e8f0fd; --warn:#b45309; --warnbg:#fef3c7; --ok:#0f766e; --okbg:#d9f2ee; --c5:#ec4899; --c20:#dc2626; --cw5:#84cc16; --cw10:#15803d; --cm5:#2563eb; --cm10:#1e3a8a; --sky:#0ea5e9; --skybg:#e0f2fe; --skyfg:#0369a1; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --buybg:#3a1d1a; --sell:#6ea2ff; --sellbg:#182640; --warn:#fbbf24; --warnbg:#3a2e0e; --ok:#4fd1c0; --okbg:#10302c; --c5:#f472b6; --c20:#f87171; --cw5:#a3e635; --cw10:#22c55e; --cm5:#60a5fa; --cm10:#818cf8; --sky:#38bdf8; --skybg:#0c2a3d; --skyfg:#7dd3fc; }} }}
* {{ box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0; padding:14px; line-height:1.5; max-width:760px; margin-inline:auto; }}
h1 {{ font-size:1.3rem; margin:4px 0 2px; }}
.t {{ color:var(--mut); font-size:.8rem; margin-bottom:14px; }}
h2 {{ font-size:1.05rem; margin:26px 0 10px; padding-left:10px; border-left:4px solid var(--fg); }}
.cards {{ display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; }}
.cl {{ font-size:.75rem; color:var(--mut); }} .cv {{ font-size:1.35rem; font-weight:700; }} .cs {{ font-size:.75rem; color:var(--mut); }}
.card.up .cv {{ color:var(--buy); }} .card.dn .cv {{ color:var(--sell); }} .card.bad {{ background:var(--warnbg); }}
.gauges {{ display:grid; gap:10px; }}
.gauge {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; border-left-width:6px; }}
.gauge.risk.lv0, .gauge.bottom.lv0 {{ border-left-color:var(--line); }}
.gauge.risk.lv1 {{ border-left-color:var(--warn); }} .gauge.risk.lv2 {{ border-left-color:var(--buy); background:var(--buybg); }}
.gauge.bottom.lv1 {{ border-left-color:var(--ok); }} .gauge.bottom.lv2 {{ border-left-color:var(--ok); background:var(--okbg); }}
.gh {{ display:flex; justify-content:space-between; font-size:1rem; font-weight:600; }}
.bar {{ height:6px; background:var(--line); border-radius:3px; margin:8px 0; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--fg); }}
.gauge.risk .bar i {{ background:var(--buy); }} .gauge.bottom .bar i {{ background:var(--ok); }}
.gauge ul {{ list-style:none; margin:0; padding:0; font-size:.83rem; }}
.gauge li {{ display:flex; gap:8px; align-items:center; padding:3px 0; }} .gauge li .t {{ flex:1; }}
.dot {{ width:10px; height:10px; border-radius:50%; background:var(--line); flex:none; }}
.gauge.risk li.on .dot {{ background:var(--buy); }} .gauge.bottom li.on .dot {{ background:var(--ok); }}
li.on {{ font-weight:600; }} li.off, li.na {{ color:var(--mut); }}
.sg {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; margin-bottom:10px; border-left-width:6px; }}
.sg h3 {{ margin:0 0 6px; font-size:1rem; }} .sg h3 small, summary small {{ color:var(--mut); font-weight:400; }}
.sg.buy {{ border-left-color:var(--buy); }} .sg.sell {{ border-left-color:var(--sell); }}
.sg.buy.strong {{ background:var(--buybg); }} .sg.sell.strong {{ background:var(--sellbg); }}
.sg.buy h3 {{ color:var(--buy); }} .sg.sell h3 {{ color:var(--sell); }}
.sig {{ padding:7px 0; border-top:1px solid var(--line); }} .sig:first-of-type {{ border-top:0; }}
.sh {{ display:flex; gap:8px; align-items:baseline; }} .sh span {{ color:var(--mut); font-size:.75rem; }} .sh em {{ margin-left:auto; font-style:normal; font-size:.8rem; }}
.ex {{ font-size:.75rem; color:var(--mut); margin-top:2px; }}
.sg.hold {{ border-left-color:var(--mut); }} .sg.hold h3 {{ color:var(--mut); }}
.sg.add {{ border-left-color:var(--buy); background:var(--buybg); }} .sg.add h3 {{ color:var(--buy); }}
.sg.hot {{ border-left-color:var(--warn); }} .sg.hot h3 {{ color:var(--warn); }}
.chip.cond.buy {{ border-color:var(--buy); color:var(--buy); }} .chip.cond.sell {{ border-color:var(--sell); color:var(--sell); }}
.chip.trig.buy {{ background:var(--buy); color:#fff; border-color:var(--buy); }} .chip.trig.sell {{ background:var(--sell); color:#fff; border-color:var(--sell); }}
.mrow {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }}
.mt {{ font-size:.72rem; padding:1px 7px; border-radius:6px; border:1.5px solid transparent; background:var(--line); color:var(--mut); white-space:nowrap; }}
.mt.up {{ background:var(--buybg); color:var(--buy); }} .mt.dn {{ background:var(--sellbg); color:var(--sell); }}
.mt.h1 {{ background:var(--buybg); color:var(--buy); }} .mt.h2 {{ background:var(--buy); color:#fff; }}
.mt.l1 {{ background:var(--skybg); color:var(--skyfg); }} .mt.l2 {{ background:var(--sky); color:#fff; }}
.mt.hot {{ background:var(--buy); color:#fff; }} .mt.cold {{ background:var(--sell); color:#fff; }}
.mt.cond {{ border-color:currentColor; font-weight:700; box-shadow:0 0 0 1px currentColor inset; }}
.mt.trig {{ font-weight:800; outline:2px solid currentColor; outline-offset:1px; }}
.chart {{ width:100%; height:auto; display:block; }} .chwrap {{ padding:0 10px 6px; }} .chwrap.kospi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; margin-top:8px; padding:8px 10px; }}
.chart.mini {{ margin-top:6px; }}
.ch-bg {{ fill:none; stroke:var(--line); }} .ch-grid {{ stroke:var(--mut); stroke-dasharray:3 3; opacity:.5; }}
.chart path {{ fill:none; stroke-linejoin:round; stroke-linecap:round; stroke-width:1px; vector-effect:non-scaling-stroke; }}
.chart line {{ vector-effect:non-scaling-stroke; stroke-width:1px; }}
.ch-close {{ stroke:var(--fg); stroke-width:1; }} .ch-ma5 {{ stroke:var(--c5); stroke-width:1; }}
.ch-ma20 {{ stroke:var(--c20); stroke-width:1; }} .ch-w5 {{ stroke:var(--cw5); stroke-width:1; }} .ch-w10 {{ stroke:var(--cw10); stroke-width:1; }}
.ch-m5 {{ stroke:var(--cm5); stroke-width:1; }} .ch-m10 {{ stroke:var(--cm10); stroke-width:1; }} .ch-rsi {{ stroke:var(--fg); stroke-width:1; }}
.ch-dot {{ fill:var(--fg); }} .ch-t {{ fill:var(--mut); font-size:9px; }} .ch-last {{ fill:var(--fg); font-size:9.5px; font-weight:700; }}
.ch-lg {{ font-size:9.5px; }} .ch-lclose {{ fill:var(--fg); }} .ch-lma5 {{ fill:var(--c5); }} .ch-lma20 {{ fill:var(--c20); }} .ch-lw5 {{ fill:var(--cw5); }} .ch-lw10 {{ fill:var(--cw10); }} .ch-lm5 {{ fill:var(--cm5); }} .ch-lm10 {{ fill:var(--cm10); }}
@media (min-width: 900px) {{
  body {{ max-width:1180px; }}
  .cards {{ grid-template-columns:repeat(4,1fr); }}
  .gauges {{ grid-template-columns:1fr 1fr; align-items:start; }}
  .sgrid, .dgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; align-items:start; }}
  .sgrid .sg, .dgrid details {{ margin-bottom:0; }}
}}
.none {{ color:var(--mut); font-size:.85rem; }}
.chip {{ display:inline-block; font-size:.72rem; padding:1px 8px; border-radius:99px; margin:2px 4px 2px 0; border:1px solid var(--line); background:var(--card); }}
.chip.trig {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }}
.chip.buy {{ background:var(--buybg); color:var(--buy); border-color:var(--buy); font-weight:600; }}
.chip.sell {{ background:var(--sellbg); color:var(--sell); border-color:var(--sell); font-weight:600; }}
.chip.flag {{ color:var(--mut); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:6px; }}
summary {{ cursor:pointer; padding:10px 12px; font-size:.9rem; }} .code {{ color:var(--mut); font-size:.75rem; }}
.rsi {{ font-size:.75rem; padding:1px 6px; border-radius:6px; background:var(--line); }}
.rsi.hot {{ background:var(--buybg); color:var(--buy); }} .rsi.cold {{ background:var(--sellbg); color:var(--sell); }}
pre {{ white-space:pre-wrap; word-break:break-all; font-size:.78rem; line-height:1.55; margin:0; padding:2px 12px 12px; font-family:ui-monospace,Menlo,Consolas,monospace; }}
pre.warn {{ background:var(--warnbg); border-radius:10px; padding:10px 12px; }}
.legend {{ font-size:.75rem; color:var(--mut); margin-top:6px; }}
</style></head><body>
<h1>장 마감 리포트</h1>
<div class="t">갱신 {stamp} · 규칙 기반 참고 신호이며 투자 판단 책임은 본인에게 있습니다</div>

<h2>시장 현황</h2>
<div class="cards">{"".join(cards)}</div>
{('<div class="chwrap kospi"><div class="cl">코스피 최근 ' + str(m.CHART_DAYS) + '거래일</div>' + svg_chart(ks["chart"]) + '</div>') if ks and ks.get("chart") else ""}

<h2>시장 위험 · 바닥 지표</h2>
<div class="gauges">{gauge("risk", "시장 위험 지표")}{gauge("bottom", "시장 바닥 지표")}</div>

<h2>오늘의 매수 · 매도 시그널</h2>
<div class="sgrid">{sig_html}</div>
<div class="legend">타점 = 조건 2개 이상 + 트리거 1개 이상 / 저평가·매도 관심 = 조건만 충족(방향 확인 전) / 하락 진행 중 = 싸 보이지만 5일 -5% 이하이거나 20일 신저가 갱신 중 / 일부 익절 = 과열 조건은 충족했지만 일·주봉이 모두 정배열이라 추세가 살아 있음(전량 매도보다 분할 익절을 검토하는 구간, 월봉은 참고). 한국 관례대로 상승·정배열·이격도 높음=빨강, 하락·역배열·이격도 낮음=파랑. 조건에 걸린 항목은 굵은 테두리, 트리거에 걸린 항목은 바깥 윤곽선으로 강조. 50일 이격 태그는 과거 범위의 높은 쪽이면 빨강(최대 대비 %), 낮은 쪽이면 하늘색(최소 대비 %), 진한 색은 최대×0.9 이상 또는 최소×1.1 이하.</div>

<h2>종목 스크리닝</h2>
<div class="dgrid">{screen_html}</div>

<h2>종목별 세부내용</h2>
<div class="dgrid">{detail_html}</div>
{extra}
</body></html>"""

os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
print("docs/index.html 생성 완료")

# ---- 텔레그램 전송 (선택) ----
token = os.environ.get("TELEGRAM_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if token and chat_id:
    text = f"장 마감 리포트 {stamp}\n\n{short_report}"
    for i in range(0, len(text), 3800):  # 텔레그램 글자 제한(4096) 대비 분할
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text[i:i + 3800]},
                timeout=15,
            )
            print("텔레그램 전송:", r.status_code, "" if r.ok else r.text[:200])
        except Exception as e:
            print("텔레그램 전송 실패:", type(e).__name__, e)
