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

snap = m.market_snapshot()
msig = m.market_signals(snap, results)
head = m.market_text(snap)
sigs = m.signals_text(results, msig)
summary = m.summary_text(results)
detail = m.detail_section(results)

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


def metrics_html(r, side, lv):
    """종목 한 줄 요약 태그. 조건에 걸린 태그는 테두리, 트리거에 걸린 태그는 채움으로 강조"""
    keys = r["sig"]["buy_k" if side == "buy" else "sell_k"]

    def tag(key, txt, color):
        mark = {"c": " cond", "t": " trig"}.get(keys.get(key), "")
        return f'<span class="mt {color}{mark}">{E(txt)}</span>'

    out = []
    d = r["disp"]
    if d:
        out.append(tag("disp", f"50일 이격 {d['cur']:.1f} ({m._n(d['min'])}/{m._n(d['max'])})", disp_cls(d["cur"])))
    for lb, k in (("일", "day"), ("주", "week"), ("월", "month")):
        x = r[k]
        out.append(tag("day" if k == "day" else "", lb + (" 정" if x["above"] else " 역") if x["ok"] else lb + " -",
                       "up" if x["ok"] and x["above"] else "dn" if x["ok"] else "n"))
    g = (r["close"] / r["ma10"] - 1) * 100
    out.append(tag("", f"10일 {abs(g):.1f}% {'▲' if g >= 0 else '▼'}" + (f" · 이탈선 {m.fmt_price(r['ma10'])}" if lv == "과열" else ""),
                   "up" if g >= 0 else "dn"))
    out.append(tag("rsi", f"RSI {r['rsi']:.0f}", "hot" if r["rsi"] >= 70 else "cold" if r["rsi"] <= 30 else "n"))
    out.append(tag("ret5", f"5일 {r['ret5']:+.1f}%", "up" if r["ret5"] > 0 else "dn"))
    out.append(tag("high", f"52주 고점 {r['from_high']:+.0f}%", "n"))
    return '<div class="mrow">' + "".join(out) + "</div>"


def sig_block(head_txt, side, lv, cls):
    hit = [r for r in results if r["sig"][side] == lv]
    items = ""
    for r in hit:
        sg = r["sig"]
        why = "".join(chip(c, "cond " + side) for c in sg[side + "_c"]) + "".join(chip(t, "trig " + side) for t in sg[side + "_t"])
        items += (f'<div class="sig"><div class="sh"><b>{E(r["name"])}</b><span>{E(r["code"])}</span>'
                  f'</div><div class="why">{why}</div>{metrics_html(r, side, lv)}</div>')
    body = items or '<div class="none">해당 종목 없음</div>'
    return f'<div class="sg {cls}"><h3>{head_txt} <small>{len(hit)}</small></h3>{body}</div>'


sig_html = (sig_block("매수 타점", "buy", "타점", "buy strong") + sig_block("매수 관심", "buy", "관심", "buy")
            + sig_block("매도 타점", "sell", "타점", "sell strong")
            + sig_block("과열 · 추세 유지 <small>(매도 아님, 이익 보호 구간)</small>", "sell", "과열", "hot")
            + sig_block("매도 관심 <small>(추세 약화)</small>", "sell", "관심", "sell"))

screen_html = ""
for h, n, lines in m.summary_sections(results):
    body = E("\n".join(lines)) if n else "해당 종목 없음"
    screen_html += f'<details><summary>{E(h)} <small>{n}</small></summary><pre>{body}</pre></details>'

detail_html = ""
for r in results:
    fl = m.flags(r)
    chips = "".join(chip(f, "buy" if f.startswith("매수") else "sell" if f.startswith("매도") else "flag") for f in fl)
    rc = "hot" if r["rsi"] >= 70 else "cold" if r["rsi"] <= 30 else ""
    detail_html += (f'<details><summary><b>{E(r["name"])}</b> <span class="code">{E(r["code"])}</span> '
                    f'<span class="rsi {rc}">RSI {r["rsi"]:.0f}</span> {chips}</summary>'
                    f'<pre>{E(m.detail_text(r))}</pre></details>')

extra = f'<pre class="warn">{E(notes.strip())}</pre>' if notes.strip() else ""

page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>장 마감 리포트</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb;
  --buy:#d92d20; --buybg:#fdecea; --sell:#1d5fd1; --sellbg:#e8f0fd; --warn:#b45309; --warnbg:#fef3c7; --ok:#0f766e; --okbg:#d9f2ee; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --buybg:#3a1d1a; --sell:#6ea2ff; --sellbg:#182640; --warn:#fbbf24; --warnbg:#3a2e0e; --ok:#4fd1c0; --okbg:#10302c; }} }}
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
.sg.hot {{ border-left-color:var(--warn); }} .sg.hot h3 {{ color:var(--warn); }}
.chip.cond.buy {{ border-color:var(--buy); color:var(--buy); }} .chip.cond.sell {{ border-color:var(--sell); color:var(--sell); }}
.chip.trig.buy {{ background:var(--buy); color:#fff; border-color:var(--buy); }} .chip.trig.sell {{ background:var(--sell); color:#fff; border-color:var(--sell); }}
.mrow {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }}
.mt {{ font-size:.72rem; padding:1px 7px; border-radius:6px; border:1.5px solid transparent; background:var(--line); color:var(--mut); white-space:nowrap; }}
.mt.up {{ background:var(--buybg); color:var(--buy); }} .mt.dn {{ background:var(--sellbg); color:var(--sell); }}
.mt.h1 {{ background:var(--buybg); color:var(--buy); }} .mt.h2 {{ background:var(--buy); color:#fff; }}
.mt.l1 {{ background:var(--sellbg); color:var(--sell); }} .mt.l2 {{ background:var(--sell); color:#fff; }}
.mt.hot {{ background:var(--buy); color:#fff; }} .mt.cold {{ background:var(--sell); color:#fff; }}
.mt.cond {{ border-color:currentColor; font-weight:700; box-shadow:0 0 0 1px currentColor inset; }}
.mt.trig {{ font-weight:800; outline:2px solid currentColor; outline-offset:1px; }}
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

<h2>시장 위험 · 바닥 지표</h2>
<div class="gauges">{gauge("risk", "시장 위험 지표")}{gauge("bottom", "시장 바닥 지표")}</div>

<h2>오늘의 매수 · 매도 시그널</h2>
{sig_html}
<div class="legend">타점 = 조건 2개 이상 + 트리거 1개 이상 / 관심 = 조건만 충족 / 과열 = 조건은 충족했지만 일·주봉이 모두 정배열이라 추세가 살아 있는 종목. 한국 관례대로 상승·정배열·이격도 높음=빨강, 하락·역배열·이격도 낮음=파랑. 조건에 걸린 항목은 굵은 테두리, 트리거에 걸린 항목은 바깥 윤곽선으로 강조.</div>

<h2>종목 스크리닝</h2>
{screen_html}

<h2>종목별 세부내용</h2>
{detail_html}
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
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text[i:i + 3800]},
            timeout=15,
        )
        print("텔레그램 전송:", r.status_code)
