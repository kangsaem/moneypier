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

render_errors = []


def guard(label, fn, *args, default=""):
    """한 부분이 실패해도 리포트 전체는 계속 만든다. 실패 내용은 페이지 하단에 표시"""
    try:
        return fn(*args)
    except Exception as e:
        render_errors.append(f"{label} 실패: {type(e).__name__}: {e}")
        return default



tickers = m.resolve_codes(m.load_tickers())
unresolved = [t["name"] for t in tickers if not t.get("code")]
tickers = [t for t in tickers if t.get("code")]

results, failed = [], []
for t in tickers:
    try:
        results.append(m.analyze(t["code"], t["name"]))
    except Exception as e:
        failed.append(f"{t['name']} ({t['code']}): {type(e).__name__}: {e}")

snap = guard("시장 지표 수집", m.market_snapshot,
             default={"ks": None, "vix": None, "y10": None, "y30": None, "idx": {}, "err": ["시장 지표 수집 실패"]})
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
# 시간대별 리포트 종류: 05:00~15:29 = 미장 마감 리포트(S&P500·나스닥 차트), 그 외 = 국장 마감 리포트(코스피·코스닥 차트)
_hm = kst.hour * 100 + kst.minute
IS_US = 500 <= _hm < 1530
TITLE = "미장 마감 리포트" if IS_US else "국장 마감 리포트"
CHART_IDX = ("spx", "ndx") if IS_US else ("kospi", "kosdaq")
E = html.escape


def chip(txt, cls):
    return f'<span class="chip {cls}">{E(txt)}</span>'


def card(label, value, sub="", cls=""):
    return f'<div class="card {cls}"><div class="cl">{E(label)}</div><div class="cv">{E(value)}</div><div class="cs">{E(sub)}</div></div>'


def updown(x):
    return "up" if x > 0 else "dn" if x < 0 else ""


def index_tags(v):
    """지수 카드 태그: 50일 이격도 숫자(과거 최소/최대) + RSI. 색은 종목 태그와 같은 규칙"""
    out = ""
    d = v.get("disp")
    if d:
        dv = m.disp_view(d)
        cls = ("h2" if dv["strong"] else "h1") if dv["side"] == "high" else ("l2" if dv["strong"] else "l1")
        out += f'<span class="mt {cls}">이격도 {d["cur"]:.1f} ({m._n(d["min"])}/{m._n(d["max"])})</span>'
    r_ = v.get("rsi")
    if r_ is not None:
        out += f'<span class="mt {"hot" if r_ >= 70 else "cold" if r_ <= 30 else "n"}">RSI {r_:.0f}</span>'
    return f'<div class="mtags">{out}</div>' if out else ""


def spark(vals, w=120, h=26):
    """작은 추이선(최근 120거래일). 끝점 강조"""
    if not vals or len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(vals))
    ex, ey = pts.split()[-1].split(",")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.4"/>'
            f'<circle cx="{ex}" cy="{ey}" r="2.2" fill="currentColor"/></svg>')


def credit_tags(cr):
    """신용잔고/예탁금 카드: 과거 대비 위치(상위 %) 태그 + 최근 120일 추이선"""
    top = max(100 - cr["pct"], 0)
    cls = "hot" if cr["pct"] >= m.CREDIT_TOP else "h1" if cr["pct"] >= 75 else "l1" if cr["pct"] <= 25 else "n"
    tip = (f"신용잔고 {cr['cred'] / 1e4:,.1f}조 / 고객예탁금 {cr['dep'] / 1e4:,.1f}조 ({cr['date']:%m-%d}) · "
           f"{cr['since']:%Y-%m} 이후 {cr['n']}거래일 중 최고 {cr['max']:.1f}% · 최저 {cr['min']:.1f}%")
    txt = "과거 최고" if top < 0.5 else "과거 최저" if cr["pct"] < 0.5 else f"과거 상위 {top:.0f}%" if cr["pct"] >= 50 else f"과거 하위 {cr['pct']:.0f}%"
    return (f'<div class="mtags" title="{E(tip)}"><span class="mt {cls}">{E(txt)}</span>'
            f'<span class="mt n">{cr["min"]:.0f}~{cr["max"]:.0f}%</span></div>{spark(cr.get("spark"))}')


def series_tags(v, rng, hot_high=True):
    """코스피 신용잔고 카드: 과거 대비 위치 + 범위 + 추이선. hot_high=False면 하위 쪽을 강조"""
    p = v["pct"]
    top = 100 - p
    txt = "과거 최고" if top < 0.5 else "과거 최저" if p < 0.5 else f"과거 상위 {top:.0f}%" if p >= 50 else f"과거 하위 {p:.0f}%"
    if hot_high:
        cls = "hot" if p >= m.CREDIT_TOP else "h1" if p >= 75 else "l1" if p <= 25 else "n"
    else:
        cls = "cold" if p <= 100 - m.CREDIT_TOP else "l1" if p <= 25 else "h1" if p >= 75 else "n"
    tip = f"{v['since']:%Y-%m} 이후 {v['n']}거래일 기준 ({v['date']:%m-%d})"
    return (f'<div class="mtags" title="{E(tip)}"><span class="mt {cls}">{E(txt)}</span>'
            f'<span class="mt n">{E(rng)}</span></div>{spark(v.get("spark"))}')


def mcard(name, value, chg_txt, ud, extra=""):
    return (f'<div class="mc {ud}"><span class="mn">{E(name)}</span>'
            f'<div class="mv"><b>{E(value)}</b><span class="mg">{E(chg_txt)}</span></div>{extra}</div>')


cards = []
ks = snap.get("ks")
idx = snap.get("idx") or {}
for key in ("kospi", "kosdaq"):      # 상단 카드: 코스피·코스닥(이격도 포함)·VIX·신용/예탁금
    v = idx.get(key)
    if v:
        cards.append(mcard(v["name"], f"{v['close']:,.2f}", f"{v['chg']:+.2f}%", updown(v["chg"]), index_tags(v)))
v = snap.get("vix")
if v:
    prev = v["last"] - v["chg"]
    cards.append(mcard("VIX", f"{v['last']:.2f}", f"{v['chg'] / prev * 100:+.2f}%" if prev else f"{v['chg']:+.2f}", updown(v["chg"])))
cr = snap.get("credit")
if cr:
    cards.append(mcard("신용/예탁금", f"{cr['ratio']:.1f}%",
                       f"{cr['chg20']:+.1f}%p·20일" if cr.get("chg20") is not None else "",
                       updown(cr.get("chg20") or 0), credit_tags(cr)))
ck = snap.get("credit_ks")
if ck:
    c20 = ck["chg20"] / (ck["cur"] - ck["chg20"]) * 100 if ck.get("chg20") is not None else None
    cards.append(mcard("코스피 신용잔고", f"{ck['cur'] / 1e4:,.1f}조", f"{c20:+.1f}%·20일" if c20 is not None else "",
                       updown(c20 or 0), series_tags(ck, f"{ck['min'] / 1e4:.0f}~{ck['max'] / 1e4:.0f}조", hot_high=True)))
for e in snap.get("err", []):
    cards.append(f'<div class="mc bad"><span class="mn">실패</span><b>{E(e)}</b></div>')


def index_chart(key):
    v = idx.get(key)
    if not v or not v.get("chart"):
        return ""
    return (f'<div class="chwrap kospi"><div class="cl">{E(v["name"])} 최근 {m.CHART_DAYS}거래일</div>'
            + svg_chart(v["chart"]) + "</div>")


def gauge(key, title):
    g = msig[key]
    rows = "".join(
        f'<li class="{"on" if st else "na" if st is None else "off"}"><span class="dot"></span>'
        f'<span class="t">{E(lb)}</span><b>{E(val)}</b></li>' for lb, val, st in g["items"])
    pct = int(100 * g["score"] / g["total"]) if g["total"] else 0
    extra = ""
    if key == "bottom" and g["cls"] != "lv0":     # 바닥 신호가 켜졌을 때만: 밸류에이션은 직접 확인(자동 수집 불가)
        extra = (f'<a class="glink" href="{E(m.PBR_LINK)}" target="_blank" rel="noopener">'
                 f'코스피200 PBR 확인 → <small>과거 대비 싼 구간인지 (indexergo)</small></a>')
    return (f'<div class="gauge {key} {g["cls"]}"><div class="gh"><span>{title}</span>'
            f'<b>{g["score"]}/{g["total"]} · {E(g["label"])}</b></div>'
            f'<div class="bar"><i style="width:{pct}%"></i></div><ul>{rows}</ul>{extra}</div>')



def disp_cls(v):
    return ("h2" if v >= 120 else "h1" if v >= 110 else "n" if v > 95 else "l1" if v > 90 else "l2")


def _metrics_html(r, side, lv):
    """종목 한 줄 요약 태그. 조건에 걸린 태그는 테두리, 트리거에 걸린 태그는 채움으로 강조"""
    keys = r["sig"][side + "_k"]

    # 채움(진한 색 + 흰 글자 + 테두리) = 이 시그널의 조건·트리거로 쓰인 칸만(둘은 같은 모양, 구분은 마우스 올림 설명).
    # 역할이 없는 칸은 값이 극단이어도 연한 색으로만 표시
    FILL = {"up": "h2", "h1": "h2", "h2": "h2", "hot": "h2", "dn": "cold", "cold": "cold",
            "l1": "l2", "l2": "l2", "n": "fn", "arr up": "h2 arr", "arr dn": "cold arr"}
    SOFT = {"h2": "h1", "l2": "l1", "hot": "up", "cold": "dn"}

    def tag(key, txt, color, title=""):
        role = keys.get(key) if key else None
        if role:
            cls = FILL.get(color, "fn") + " role"     # 조건·트리거 같은 모양: 진한 채움 + 흰 글자 + 테두리
            title = (title + " · " if title else "") + ("이 시그널의 트리거" if role == "t" else "이 시그널의 조건")
        else:
            cls = SOFT.get(color, color)
        ttl = f' title="{E(title)}"' if title else ""
        return f'<span class="mt {cls}"{ttl}>{E(txt)}</span>'

    out = []
    d = r["disp"]
    if d:
        v = m.disp_view(d)
        if v["side"] == "high":      # 높은 쪽 = 빨강, 과거 최대 대비 %
            cls = "h2" if v["strong"] else "h1"
        else:                        # 낮은 쪽 = 하늘색, 과거 최소 대비 %
            cls = "l2" if v["strong"] else "l1"
        txt = f"이격도 {d['cur']:.1f} ({m._n(d['min'])}~{m._n(d['max'])})"
        out.append(tag("disp", txt, cls, f"50일 이격도 현재 {d['cur']:.1f} · 과거 최소 {d['min']:.1f} / 최대 {d['max']:.1f}"
                                         f" · 높음 기준 {d['max_thr']:.1f} 이상 / 낮음 기준 {d['min_thr']:.1f} 이하"))
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
    # 일/주/월 5·10 배열: 칸 3개 (정=빨간 테두리, 역=파란 테두리). 순서대로 일·주·월
    for k, nm in (("day", "일봉"), ("week", "주봉"), ("month", "월봉")):
        x = r[k]
        txt, cls = ("정", "arr up") if x["ok"] and x["above"] else ("역", "arr dn") if x["ok"] else ("-", "n")
        out.append(tag(k if k in ("day", "week") else "", txt + (day_note if k == "day" else ""), cls, f"{nm} 5·10선 배열"))
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
    out.append(tag("rsi", f"RSI {r['rsi']:.0f}{rsi_note}", "hot" if r["rsi"] >= r["sig"]["rsi_hi"] else "cold" if r["rsi"] <= r["sig"]["rsi_lo"] else "n"))
    out.append(tag("ret5", f"5일 {r['ret5']:+.1f}%", "up" if r["ret5"] > 0 else "dn"))
    out.append(tag("high", f"52주 고점 {r['from_high']:+.0f}%", "dn" if r["from_high"] <= -20 else "n",
                   f"52주 최고가 {m.fmt_price(r['high52'])}"))
    if r.get("from_low") is not None:
        out.append(tag("", f"52주 저점 {r['from_low']:+.0f}%", "n", f"52주 최저가 {m.fmt_price(r['low52'])}"))
    bs = r.get("bottom") or 0
    if bs and r.get("bottom_near"):
        bi = r.get("bottom_info") or {}
        why = {1: "하락 멈춤: 저점 뒤 20거래일 신저가 없음 + 종가가 10일선 위",
               2: "바닥 다지기: 하락 멈춤 + 쌍바닥 또는 RSI 상승 다이버전스 + 20일선 상승 전환",
               3: "추세 전환: 하락 멈춤 + 5주>10주 + 종가가 10주선 위 + 10주선 하락 멈춤"}[bs]
        low = f" · 저점 {m.fmt_price(bi['low'])} ({bi['low_date']:%y.%m.%d}, 1년 고점 대비 {bi['drop']:.0f}%, {bi['since']}거래일 전)" if bi.get("low") else ""
        out.append(tag("bottom", "바닥근접", "bt2", f"단계 {bs}/3 · " + why + low))
    ts = r.get("top") or 0
    if ts and r.get("top_near"):
        ti = r.get("top_info") or {}
        why = {1: "상승 멈춤: 고점 뒤 신고가 없음(과열 고점이면 바로) + 종가가 10일선 아래",
               2: "꼭지 다지기: 상승 멈춤 + 쌍봉 또는 RSI 하락 다이버전스 + 20일선 하락 전환",
               3: "추세 전환: 상승 멈춤 + 5주<10주 + 종가가 10주선 아래 + 10주선 상승 멈춤"}[ts]
        hi = f" · 고점 {m.fmt_price(ti['high'])} ({ti['high_date']:%y.%m.%d}, 1년 저점 대비 +{ti['rise']:.0f}%, {ti['since']}거래일 전)" if ti.get("high") else ""
        out.append(tag("top", "꼭지근접", "tp2", f"단계 {ts}/3 · " + why + hi))
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


LINES = (("5일", "ma5"), ("20일", "ma20"), ("5주", "w5"), ("10주", "w10"), ("5월", "m5"), ("10월", "m10"))
PAIRS = (("ma5", "ma20"), ("w5", "w10"), ("m5", "m10"))


def _last(ch, k):
    return ch[k][-1] if ch.get(k) else None


def legend_html(ch):
    """차트 위 범례(HTML): 오늘 종가 + 일/주/월 이평선 색"""
    sw = lambda k: f'<i class="sw sw-{k}"></i>'
    parts = [f'<span class="lgi">{sw("close")}오늘 종가</span>']
    for (la, a), (lb, b) in zip(LINES[0::2], LINES[1::2]):
        parts.append(f'<span class="lgi">{sw(a)}{la} {sw(b)}{lb}</span>')
    if ch.get("proj") and any(ch["proj"].values()):
        parts.append('<span class="lgn">점선 = 가격 유지 가정</span>')
    return '<div class="lg">' + "".join(parts) + "</div>"


def _svg_chart(ch, mini=False):
    """인라인 SVG: 이평선(5·20일, 5·10주, 5·10월) + 오늘 종가 점 + RSI 패널.
    오른쪽 여분 = 가격이 오늘 수준에 머문다고 가정한 주·월선의 앞으로의 경로(점선).
    오늘 종가·RSI 숫자는 그림 영역 안쪽 오른쪽 끝(앞으로 구간)에 표시"""
    if not ch or len(ch["close"]) < 5:
        return ""
    g = lambda k: ch[k]
    keys = [k for _, k in LINES]
    close = g("close")
    n = len(close)
    proj = ch.get("proj") or {}
    fdates = ch.get("fdates") or []
    F = len(fdates) if any(proj.values()) else 0
    N = n + F
    W = 660
    PH = 170
    RH = 54 if ch.get("rsi") else 0
    gap = 10 if RH else 0
    pl, pt, pb = 4, 4, 16
    H = pt + PH + gap + RH + pb
    xr = W - 2                                      # 그림 영역 오른쪽 끝
    vals = [v for k in keys for v in g(k) if v is not None] + [close[-1]]
    vals += [v for k in proj if proj[k] for v in proj[k] if v is not None]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1
    m_ = (hi - lo) * 0.06
    lo, hi = lo - m_, hi + m_
    x = lambda i: pl + (xr - pl - 2) * i / (N - 1)
    y = lambda v: pt + PH * (1 - (v - lo) / (hi - lo))
    tx = xr - 4                                     # 안쪽 숫자 라벨 x (오른쪽 정렬)
    o = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="가격 차트">']
    o.append(f'<rect class="ch-bg" x="0" y="{pt}" width="{xr}" height="{PH}"/>')
    if F:   # 앞으로 구간 옅은 배경
        o.append(f'<rect class="ch-fc" x="{x(n - 1):.1f}" y="{pt}" width="{xr - x(n - 1):.1f}" height="{PH}"/>')
    # 월 경계 세로선 + 월 숫자
    alld = list(ch["dates"]) + list(fdates[:F])
    for i in range(1, N):
        if alld[i][3:5] != alld[i - 1][3:5]:
            xv = x(i)
            o.append(f'<line class="ch-vl" x1="{xv:.1f}" x2="{xv:.1f}" y1="{pt}" y2="{pt + PH}"/>')
            o.append(f'<text class="ch-mo" x="{xv + 2:.1f}" y="{pt + PH - 3}">{int(alld[i][3:5])}</text>')
    for k in keys[::-1]:
        o.append(f'<path class="ch-{k}" d="{_path(g(k), x, y)}"/>')
        if F and proj.get(k):
            xf = lambda i, k=k: x(n - 1 + i)
            o.append(f'<path class="ch-{k} ch-f" d="{_path(proj[k], xf, y)}"/>')
    ly = y(close[-1])
    o.append(f'<circle class="ch-dot" cx="{x(n - 1):.1f}" cy="{ly:.1f}" r="3"/>')
    # 오늘 종가: 검은 점 바로 오른쪽, 카드 숫자와 같은 크기(HTML로 겹쳐 그려 화면 폭과 무관하게 크기 유지)
    lbl = (f'<span class="lastlbl" style="left:{(x(n - 1) + 6) / W * 100:.2f}%;top:{ly / H * 100:.2f}%">'
           f'{m.fmt_price(close[-1])}</span>')
    o.append(f'<text class="ch-t" x="{pl}" y="{H - 3}">{ch["dates"][0]}</text>')
    o.append(f'<text class="ch-t" x="{x(n - 1):.1f}" y="{H - 3}" text-anchor="middle">{ch["dates"][-1]}</text>')
    if RH:
        top = pt + PH + gap
        y2 = lambda v: top + RH * (1 - v / 100)
        o.append(f'<rect class="ch-bg" x="0" y="{top}" width="{xr}" height="{RH}"/>')
        rv = g("rsi")
        last = next((v for v in reversed(rv) if v is not None), None)
        for lv in (30, 70):
            o.append(f'<line class="ch-grid" x1="0" x2="{xr}" y1="{y2(lv):.1f}" y2="{y2(lv):.1f}"/>')
            if last is None or abs(y2(lv) - y2(last)) > 9:      # RSI 값 라벨과 겹치면 기준선 숫자는 생략
                o.append(f'<text class="ch-t ch-in" x="{tx}" y="{y2(lv) + 3:.1f}" text-anchor="end">{lv}</text>')
        o.append(f'<path class="ch-rsi" d="{_path(rv, x, y2)}"/>')
        if last is not None:
            o.append(f'<text class="ch-last ch-in" x="{tx}" y="{y2(last) + 3:.1f}" text-anchor="end">RSI {last:.0f}</text>')
    o.append("</svg>")
    return legend_html(ch) + '<div class="svgbox">' + "".join(o) + lbl + "</div>"


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


REV_SIDE = {"buyb": "buy", "buy": "buy", "up": "add", "sell": "sell", "caution": "sell", "hold": "buy", "wait": "buy", "waitdn": "buy"}
REV_CLS = {"buyb": "buy strong", "buy": "buy strong", "up": "buy strong", "sell": "sell strong", "caution": "warn", "hold": "hold-ok",
           "wait": "waitc", "waitdn": "downc"}
REV_NOTE = {"sell": "매도·비중축소 + 상대강도↓ 또는 익절검토 + 거래량 폭증 → 전량 매도 (손절 -8%와 함께)",
            "buyb": "깊이 빠졌다 돌아서는 자리(근거 3개 이상: 매수 신호/눌림진행 · 싼 조건 3개 모두 · 장기 추세 상승 · 바닥근접) — 1개월 반등이 강했지만 다시 밀릴 수 있음",
            "buy": "매수 신호(싼 조건 2개 이상 + 반등 트리거) 또는 눌림진행(싸고 아직 빠지는 중이지만 장기 추세 상승)",
            "up": "오르는 추세 속 20일선 눌림 뒤 반등 · 과열 아님",
            "hold": "주봉·월봉 정배열 + 종가 10주선 위 + 경고 없음 — 그냥 들고 가기. 진한 연두 = 일봉도 정배열(순항), 옅은 연두 = 일봉만 역배열(조정, 팔 이유 아님)",
            "caution": "과열 — 좋은 종목이라도 지금은 비쌈. 보유 유지, 새로 사거나 더 사지 않음",
            "wait": "싼 조건 2개 이상 · 하락은 멈췄지만 반등 신호 전",
            "waitdn": "싸지만 아직 하락 중이고 장기 추세도 하락 — 반등 확인 전까지 손대지 않음"}


def rev_block(key, head_txt):
    hit = [r for r in results if (r.get("review") or {}).get(key)]
    if not hit:            # 해당 종목이 없는 칸은 화면에 표시하지 않음
        return ""
    side = REV_SIDE[key]
    if key == "hold":      # 보유: 차트 없이 이름 칩만 한 줄로
        chips_ = "".join(
            f'<span class="hchip {"h2" if r["review"][key]["level"] == "순항" else "h1"}" '
            f'title="{E(r["review"][key]["level"])} · RSI {r["rsi"]:.0f}'
            + (f' · 이격도 {r["disp"]["cur"]:.1f}' if r.get("disp") else "") + f' · 5일 {r["ret5"]:+.1f}%">'
            f'{E(r["name"])}</span>' for r in sorted(hit, key=lambda r: r["review"][key]["level"] != "순항"))
        return (f'<div class="sg {REV_CLS[key]}"><h3>{head_txt} <small>{len(hit)}</small></h3>'
                f'<div class="ex">{E(REV_NOTE[key])}</div><div class="hchips">{chips_}</div></div>')
    items = ""
    for r in hit:
        rv = r["review"][key]
        why = "".join(f'<span class="why {key}">{E(w)}</span>' for w in rv["why"])
        lv = r["sig"].get(side) if side != "add" else None
        items += (f'<div class="sig"><div class="sh"><b>{E(r["name"])}</b><span>{E(r["code"])}</span></div>'
                  f'<div class="whys">{why}</div>{metrics_html(r, side, lv)}<div class="chwrap sigch">{svg_chart(r["chart"])}</div></div>')
    return (f'<div class="sg {REV_CLS[key]}"><h3>{head_txt} <small>{len(hit)}</small></h3>'
            f'<div class="ex">{E(REV_NOTE[key])}</div>{items}</div>')


# 순서: 매도(전량) / 매수(바닥) / 매수(눌림) / 매수(추세) / 보유 / 매수보류 / 반등대기 / 하락  (market_report.REVIEW와 같은 순서)
sig_html = "".join(rev_block(k, nm) for k, nm, _ in m.REVIEW)

# 종목 스크리닝(크로스·과열·8% 변동·이격도 최대/최소권)은 2026-10-11 뺌 — 분류 탭·종목 칩과 중복

detail_html = ""
for r in results:
    fl = guard(f"{r['name']} 태그", m.flags, r, default=[])
    tone = {nm: t for nm, t, _ in m.review_groups(r)}
    chips = "".join(chip(f, {"buy": "buy", "sell": "sell", "wait": "wait", "down": "down", "warn": "warn",
                             "hold2": "hold2", "hold1": "hold1"}.get(tone.get(f), "flag")) for f in fl)
    tones = [t for _, t, _ in m.review_groups(r) if t not in ("wait", "down")]
    # 배경: 매수 연분홍 / 매도 연하늘 / 매수보류 호박색 / 보유 연두(순항 진하게, 조정 옅게)
    dcls = {"buy": ' class="dbuy"', "sell": ' class="dsell"', "warn": ' class="dwarn"',
            "hold2": ' class="dhold2"', "hold1": ' class="dhold1"'}.get(tones[0] if tones else "", "")
    rc = "hot" if r["rsi"] >= r["sig"]["rsi_hi"] else "cold" if r["rsi"] <= r["sig"]["rsi_lo"] else ""
    detail_html += (f'<details{dcls}><summary><b>{E(r["name"])}</b> <span class="code">{E(r["code"])}</span> '
                    f'<span class="rsi {rc}">RSI {r["rsi"]:.0f}</span> {chips}</summary>'
                    f'<div class="chwrap">{svg_chart(r["chart"])}</div><pre>{E(guard(r['name'] + " 세부내용", m.detail_text, r))}</pre></details>')

# ---- 연간 성적표: 올해 1/1~오늘(12/31이면 1년)과 지난 해들 — 내 종목을 1/1에 똑같이 나눠 샀을 때 vs 주요 지수
YEAR_IDX = (("kospi", "코스피", "KS11", "^KS11"), ("kosdaq", "코스닥", "KQ11", "^KQ11"),
            ("spx", "S&P500", "US500", "^GSPC"), ("ndx", "나스닥", "IXIC", "^IXIC"))
YEARS_BACK = 5


def _year_ret(s, y):
    """y년 수익률: 전년 마지막 종가 → y년 마지막 종가(올해면 오늘). 전년 자료가 없으면 None"""
    s = s.dropna()
    prev = s[s.index.year < y]
    cur = s[s.index.year == y]
    if not len(prev) or not len(cur):
        return None
    return float((cur.iloc[-1] / prev.iloc[-1] - 1) * 100)


def year_table():
    this = kst.year
    years = list(range(this, this - YEARS_BACK - 1, -1))
    idx_s = {}
    for k, nm, fc, yc in YEAR_IDX:
        try:
            idx_s[k] = m._index_close(fc, yc)
        except Exception:
            idx_s[k] = None
    rows = ""
    for y in years:
        rets = [x for x in (_year_ret(r["_c"], y) for r in results if r.get("_c") is not None) if x is not None]
        me = sum(rets) / len(rets) if rets else None
        cells = [(me, "me")] + [(_year_ret(idx_s[k], y) if idx_s.get(k) is not None else None, "") for k, *_ in YEAR_IDX]
        if all(v is None for v, _ in cells):      # 자료가 없는 해는 줄을 만들지 않음
            continue
        beat = [k for (v, _), (k, *_) in zip(cells[1:], YEAR_IDX) if me is not None and v is not None and me > v]
        rows += (f'<tr><td class="c"><b>{y}{" (1/1~오늘)" if y == this else ""}</b><br><small>{len(rets)}종목</small></td>'
                 + "".join(f'<td class="{cls} {"up" if v and v > 0 else "dn" if v and v < 0 else ""}">{"-" if v is None else f"{v:+.1f}%"}</td>' for v, cls in cells)
                 + f'<td class="c"><small>{E(", ".join(nm for k, nm, *_ in YEAR_IDX if k in beat)) or "-"}</small></td></tr>')
    head = "".join(f"<th>{E(nm)}</th>" for _, nm, *_ in YEAR_IDX)
    return (f'<div class="wrap yr"><table><tr><th class="c">연도</th><th>내 종목 (1/1 똑같이 나눠 보유)</th>{head}<th class="c">이긴 지수</th></tr>{rows}</table></div>'
            '<p class="legend">내 종목 = 지금 목록의 종목을 그해 1월 1일(전년 마지막 종가)에 같은 금액씩 사서 들고 있었을 때의 평균 수익률(그때 상장 전이면 제외, 배당 제외). '
            '지금 목록 기준이라 지난 해일수록 "결과를 알고 고른" 효과가 섞임 — 올해 칸이 진짜 성적. 로직대로 매매했을 때의 연도별 성적은 내 종목 백테스트 계좌 페이지에.</p>')


year_html = guard("연간 성적표", year_table, default="")
# 폭락장 배너는 2026-10-11 뺌 — 시장 바닥 지표를 보고 직접 판단

if render_errors:
    notes += "\n\n[화면 생성 중 오류]\n" + "\n".join(sorted(set(render_errors)))
extra = f'<pre class="warn">{E(notes.strip())}</pre>' if notes.strip() else ""

wide_link = "".join(f' &nbsp;·&nbsp; <a href="{f}.html">{t} →</a>'
                    for f, t in (("backtest_wide_252d", "코스피 상위 · 1년"), ("backtest_wide_756d", "코스피 상위 · 3년"),
                                 ("backtest_wide", "코스피 상위 · 5년"), ("backtest_nasdaq_252d", "나스닥 · 1년"),
                                 ("backtest_nasdaq_756d", "나스닥 · 3년"), ("backtest_nasdaq", "나스닥 · 5년"),
                                 ("backtest_mine", "내 종목 (수동 실행)"), ("backtests", "백테스트 기록·비교"),
                                 ("crash_study", "폭락장 표시 뒤 성과"))
                    if os.path.exists(f"results/{f}.html"))

page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb;
  --buy:#d92d20; --buybg:#fdecea; --sell:#1d5fd1; --sellbg:#e8f0fd; --warn:#b45309; --warnbg:#fef3c7; --ok:#0f766e; --okbg:#d9f2ee; --c5:#ec4899; --c20:#dc2626; --cw5:#84cc16; --cw10:#15803d; --cm5:#2563eb; --cm10:#1e3a8a; --sky:#0ea5e9; --skybg:#e0f2fe; --skyfg:#0369a1;
  --buyfill:#d92d20; --sellfill:#1d5fd1; --skyfill:#0284c7; --mutfill:#6b7380;
  --amber:#c27803; --amberbg:#fdf3dc; --ok2bg:#d8f0c2; --ok1bg:#eef8e4; --okfg:#3c7a13; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38;
  --buy:#ff6b5e; --buybg:#3a1d1a; --sell:#6ea2ff; --sellbg:#182640; --warn:#fbbf24; --warnbg:#3a2e0e; --ok:#4fd1c0; --okbg:#10302c; --c5:#f472b6; --c20:#f87171; --cw5:#a3e635; --cw10:#22c55e; --cm5:#60a5fa; --cm10:#818cf8; --sky:#38bdf8; --skybg:#0c2a3d; --skyfg:#7dd3fc;
  --buyfill:#c62a1f; --sellfill:#2856b8; --skyfill:#0369a1; --mutfill:#525a68;
  --amber:#f0b44a; --amberbg:#352a12; --ok2bg:#22381a; --ok1bg:#1a2615; --okfg:#9fd774; }} }}
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
.sg.tp {{ border-left-color:var(--sky); }} .sg.tp h3 {{ color:var(--skyfg); }}
.chip.cond.buy {{ border-color:var(--buy); color:var(--buy); }} .chip.cond.sell {{ border-color:var(--sell); color:var(--sell); }}
.chip.trig.buy {{ background:var(--buy); color:#fff; border-color:var(--buy); }} .chip.trig.sell {{ background:var(--sell); color:#fff; border-color:var(--sell); }}
.dots {{ margin-left:auto; font-style:normal; letter-spacing:1px; font-size:.85rem; }}
.sg.buy .dots {{ color:var(--buy); }} .sg.sell .dots {{ color:var(--sell); }}
.whys {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:4px; }}
.why {{ font-size:.72rem; padding:1px 7px; border-radius:6px; font-weight:600; border:1px solid var(--line); background:var(--card); }}
.why.buy {{ color:var(--buy); border-color:var(--buy); }} .why.sell {{ color:var(--sell); border-color:var(--sell); }}
.why.caution {{ color:var(--amber); border-color:var(--amber); }} .why.up {{ color:var(--buy); }} .why.wait, .why.waitdn {{ color:var(--mut); }}
.mrow {{ display:flex; flex-wrap:wrap; gap:4px; margin-top:5px; }}
.glink {{ display:block; margin-top:8px; font-size:.82rem; color:var(--fg); text-decoration:none; border-top:1px solid var(--line); padding-top:8px; }}
.glink small {{ color:var(--mut); }} .glink:hover {{ text-decoration:underline; }}
.spark {{ display:block; margin-top:4px; color:var(--mut); max-width:100%; }}
.mt {{ font-size:.72rem; padding:1px 7px; border-radius:6px; border:1.5px solid transparent; background:var(--line); color:var(--mut); white-space:nowrap; }}
.mt.up {{ background:var(--buybg); color:var(--buy); }} .mt.dn {{ background:var(--sellbg); color:var(--sell); }}
.mt.h1 {{ background:var(--buybg); color:var(--buy); }} .mt.h2 {{ background:var(--buyfill); color:#fff; }}
.mt.bt1, .mt.bt2, .mt.bt3 {{ background:var(--card); color:var(--buy); border-color:var(--buy); font-weight:600; }}
.mt.bt2 {{ background:var(--buybg); }} .mt.bt3 {{ background:var(--buybg); border-width:2px; font-weight:800; }}
.mt.tp1, .mt.tp2, .mt.tp3 {{ background:var(--card); color:var(--sell); border-color:var(--sell); font-weight:600; }}
.mt.tp2 {{ background:var(--sellbg); }} .mt.tp3 {{ background:var(--sellbg); border-width:2px; font-weight:800; }}
.mt.l1 {{ background:var(--skybg); color:var(--skyfg); }} .mt.l2 {{ background:var(--skyfill); color:#fff; }}
.mt.hot {{ background:var(--buyfill); color:#fff; }} .mt.cold {{ background:var(--sellfill); color:#fff; }}
.mt.fn {{ background:var(--mutfill); color:#fff; }} .mt.h2, .mt.cold, .mt.l2, .mt.fn, .mt.hot {{ font-weight:700; border-color:transparent; }}
.mt.role {{ border-color:var(--fg); }}
.chart {{ width:100%; height:auto; display:block; }} .chwrap {{ padding:0 10px 6px; }} .chwrap.kospi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; margin-top:8px; padding:8px 10px; }}
.chart.mini {{ margin-top:6px; }}
.ch-bg {{ fill:none; stroke:var(--line); }} .ch-grid {{ stroke:var(--mut); stroke-dasharray:3 3; opacity:.5; }}
.chart path {{ fill:none; stroke-linejoin:round; stroke-linecap:round; stroke-width:1px; vector-effect:non-scaling-stroke; }}
.chart line {{ vector-effect:non-scaling-stroke; stroke-width:1px; }}
.ch-close {{ stroke:var(--fg); stroke-width:1; }} .ch-ma5 {{ stroke:var(--c5); stroke-width:1; }}
.ch-ma20 {{ stroke:var(--c20); stroke-width:1; }} .ch-w5 {{ stroke:var(--cw5); stroke-width:1; }} .ch-w10 {{ stroke:var(--cw10); stroke-width:1; }}
.ch-m5 {{ stroke:var(--cm5); stroke-width:1; }} .ch-m10 {{ stroke:var(--cm10); stroke-width:1; }} .ch-rsi {{ stroke:var(--fg); stroke-width:1; }}
.ch-dot {{ fill:var(--fg); }} .chart path.ch-f {{ stroke-dasharray:3 3; }} .ch-fc {{ fill:var(--fg); opacity:.035; }}
.ch-vl {{ stroke:var(--fg); opacity:.09; }} .ch-mo {{ fill:var(--mut); opacity:.85; font-size:9px; }}
.ch-in {{ paint-order:stroke; stroke:var(--card); stroke-width:3px; stroke-linejoin:round; }}
.lg {{ display:flex; flex-wrap:wrap; gap:4px 14px; align-items:center; font-size:.72rem; color:var(--mut); margin:4px 0 2px; }}
.lgi {{ display:inline-flex; align-items:center; gap:3px; white-space:nowrap; }} .lgn {{ margin-left:auto; font-size:.68rem; }}
.sw {{ display:inline-block; width:9px; height:9px; border-radius:2px; }} .sw-close {{ background:var(--fg); border-radius:50%; }}
.sw-ma5 {{ background:var(--c5); }} .sw-ma20 {{ background:var(--c20); }} .sw-w5 {{ background:var(--cw5); }} .sw-w10 {{ background:var(--cw10); }} .sw-m5 {{ background:var(--cm5); }} .sw-m10 {{ background:var(--cm10); }}
.pj {{ font-size:.7rem; padding:0 5px; border-radius:4px; color:#fff; margin-left:2px; }} .pj.up {{ background:var(--buy); }} .pj.dn {{ background:var(--sell); }}
.mt.arr {{ font-weight:700; }} .mt.arr.up {{ border-color:var(--buy); }} .mt.arr.dn {{ border-color:var(--sell); }}
.chwrap.sigch {{ padding:2px 0 0; }}
.ch-t {{ fill:var(--mut); font-size:9px; }} .ch-last {{ fill:var(--fg); font-size:9.5px; font-weight:700; }}
.ch-lg {{ font-size:9.5px; }} .ch-lclose {{ fill:var(--fg); }} .ch-lma5 {{ fill:var(--c5); }} .ch-lma20 {{ fill:var(--c20); }} .ch-lw5 {{ fill:var(--cw5); }} .ch-lw10 {{ fill:var(--cw10); }} .ch-lm5 {{ fill:var(--cm5); }} .ch-lm10 {{ fill:var(--cm10); }}
@media (min-width: 900px) {{
  body {{ max-width:1180px; }}
  .cards {{ grid-template-columns:repeat(4,1fr); }}
  .idxch {{ grid-template-columns:1fr 1fr; }}
  .gauges {{ grid-template-columns:1fr 1fr; align-items:start; }}
  .dgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; align-items:start; }}
  .dgrid details {{ margin-bottom:0; }}
  .sgrid {{ columns:2; column-gap:10px; }} .sgrid .sg {{ break-inside:avoid; }}
}}
.none {{ color:var(--mut); font-size:.85rem; }}
.chip {{ display:inline-block; font-size:.72rem; padding:1px 8px; border-radius:99px; margin:2px 4px 2px 0; border:1px solid var(--line); background:var(--card); }}
.chip.trig {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }}
.chip.buy {{ background:var(--buybg); color:var(--buy); border-color:var(--buy); font-weight:600; }}
.chip.sell {{ background:var(--sellbg); color:var(--sell); border-color:var(--sell); font-weight:600; }}
.chip.flag {{ color:var(--mut); }} .chip.flag.watch {{ color:var(--fg); border-color:var(--mut); }}
details.dwarn {{ background:var(--amberbg); }} details.dhold2 {{ background:var(--ok2bg); }} details.dhold1 {{ background:var(--ok1bg); }}
.chip.warn {{ background:var(--amberbg); color:var(--amber); border-color:var(--amber); font-weight:600; }}
.chip.hold2 {{ background:var(--ok2bg); color:var(--okfg); border-color:var(--okfg); font-weight:600; }}
.chip.hold1 {{ background:var(--ok1bg); color:var(--okfg); border-color:var(--okfg); }}
.sg.warn {{ border-left-color:var(--amber); background:var(--amberbg); }} .sg.warn h3 {{ color:var(--amber); }}
.sg.hold-ok {{ border-left-color:var(--okfg); }} .sg.hold-ok h3 {{ color:var(--okfg); }}
.sg.waitc {{ border-left-color:#8b7fd6; }} .sg.waitc h3 {{ color:#7c6fd0; }} .sg.downc {{ border-left-color:#7cc3e8; }} .sg.downc h3 {{ color:#3b9bcf; }}
.chip.wait {{ background:#efedfc; color:#6a5cc7; border-color:#8b7fd6; }} .chip.down {{ background:#e9f6fc; color:#2f8fc4; border-color:#7cc3e8; }}
.why.buyb {{ color:var(--buy); border-color:var(--buy); }}
.crash {{ background:var(--sellbg); border:2px solid var(--sell); color:var(--sell); border-radius:12px; padding:10px 14px; font-weight:700; margin:10px 0; }}
.crash small {{ font-weight:500; color:var(--mut); display:block; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
.yr table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} .yr th, .yr td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
.yr th {{ color:var(--mut); font-weight:500; }} .yr td.c, .yr th.c {{ text-align:left; white-space:normal; }} .yr small {{ color:var(--mut); }}
.yr td.up {{ color:var(--buy); }} .yr td.dn {{ color:var(--sell); }} .yr td.me {{ font-weight:700; }}
.hchips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:6px; }}
.hchip {{ font-size:.82rem; padding:3px 10px; border-radius:99px; color:var(--okfg); border:1px solid var(--okfg); font-weight:600; }}
.hchip.h2 {{ background:var(--ok2bg); }} .hchip.h1 {{ background:var(--ok1bg); font-weight:500; border-style:dashed; }}
details.dbuy {{ background:var(--buybg); }} details.dsell {{ background:var(--sellbg); }} details.dhot {{ background:var(--warnbg); }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin-bottom:6px; }}
summary {{ cursor:pointer; padding:10px 12px; font-size:.9rem; }} .code {{ color:var(--mut); font-size:.75rem; }}
.rsi {{ font-size:.75rem; padding:1px 6px; border-radius:6px; background:var(--line); }}
.rsi.hot {{ background:var(--buybg); color:var(--buy); }} .rsi.cold {{ background:var(--sellbg); color:var(--sell); }}
pre {{ white-space:pre-wrap; word-break:break-all; font-size:.78rem; line-height:1.55; margin:0; padding:2px 12px 12px; font-family:ui-monospace,Menlo,Consolas,monospace; }}
pre.warn {{ background:var(--warnbg); border-radius:10px; padding:10px 12px; }}
.legend {{ font-size:.75rem; color:var(--mut); margin-top:6px; }}
.sec {{ margin-top:14px; }}
:root {{ --bignum:1.15rem; }}
.mcards {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }}
@media (min-width:700px) {{ .mcards {{ grid-template-columns:repeat(4,minmax(0,1fr)); }} }}
.mc {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:5px 8px; display:flex; flex-direction:column; line-height:1.3; }}
.mc .mn {{ font-size:.72rem; color:var(--mut); }} .mc .mv {{ display:flex; flex-wrap:wrap; align-items:baseline; column-gap:6px; }}
.mc b {{ font-size:var(--bignum); }} .mc .mg {{ font-size:.75rem; }} .mc .mtags {{ display:flex; flex-wrap:wrap; gap:3px; margin-top:3px; }} .mc .mt {{ white-space:normal; }}
.svgbox {{ position:relative; }} .lastlbl {{ position:absolute; transform:translateY(-50%); font-size:var(--bignum); font-weight:700; line-height:1; white-space:nowrap; color:var(--fg); text-shadow:0 0 3px var(--card),0 0 3px var(--card),0 0 2px var(--card); pointer-events:none; }}
.mc.up .mg, .mc.up b {{ color:var(--buy); }} .mc.dn .mg, .mc.dn b {{ color:var(--sell); }} .mc.bad {{ background:var(--warnbg); }}
.idxch {{ display:grid; gap:8px; }}
</style></head><body>
<h1>{TITLE}</h1>
<div class="t">갱신 {stamp} · 규칙 기반 참고 신호이며 투자 판단 책임은 본인에게 있습니다</div>

<div class="mcards">{"".join(cards)}</div>
<div class="idxch">{"".join(index_chart(k) for k in CHART_IDX)}</div>

<div class="gauges sec">{gauge("risk", "시장 과열 지표")}{gauge("bottom", "시장 바닥 지표")}</div>

<div class="sgrid sec">{sig_html or '<div class="none">오늘 해당하는 시그널이 없습니다</div>'}</div>
<div class="legend">매매 규칙(v1, 백테스트로 정함): 매수(바닥)·매수(눌림)·매수(추세)에 사고, 매도(전량) 또는 손절 -8%(종가 기준, 다음 날 회복 못 하면)에 전부 판다.
국장은 신호 당일 시간외 종가(15:40 전 주문), 미장은 다음 날 LOC(지정가 = 종가 +8%). 매수보류는 보유 유지·추가 매수 없음, 보유는 그냥 들고 가기.
매도(전량) = 매도·비중축소에 상대강도↓(최근 60거래일 수익률이 지수보다 낮음)가 겹치거나, 익절검토에 거래량 폭증(최근 5일 중 20일 평균의 2.5배 이상)이 겹칠 때.
매수(바닥) = 근거 3개 이상(매수 신호/눌림진행 · 싼 조건 3개 모두 · 장기 추세 상승 · 바닥근접) — 1개월 반등이 강했지만 60일 뒤엔 우위가 사라짐. 매수(눌림) = 그 외 매수 신호·눌림진행. 매수(추세) = 오르는 추세 속 20일선 눌림 뒤 반등.
보유(연두) = 주봉·월봉 정배열 + 종가 10주선 위 + 경고 없음 — 진한 연두 = 일봉도 정배열(순항), 옅은 연두(점선) = 일봉만 역배열(조정, 팔 이유 아님). 반등대기(연보라) = 싸고 하락은 멈췄지만 반등 신호 전. 하락(연하늘) = 싸지만 하락 중이고 장기 추세도 하락.
바닥근접 / 꼭지근접 = 1년 고점 대비 25% 이상 빠진(50% 이상 오른) 종목이 돌아서는 중이고, 가격이 아직 저점 +30%(고점 -20%) 안에 있을 때만 표시 — 참고.
이격도 = 50일 이격도 현재값 (과거 최소~최대). 한국 관례대로 상승·정배열·이격도 높음=빨강, 하락·역배열·이격도 낮음=파랑. 진한 채움 칸 = 이 판정의 조건·트리거로 쓰인 항목.</div>

<h2>연간 성적표</h2>
{year_html}


<h2>종목별 세부내용</h2>
<div class="dgrid">{detail_html}</div>
<div class="legend" style="margin-top:14px"><a href="history.html"><b>신호 기록(지난 신호와 그 뒤 결과) →</b></a> &nbsp;·&nbsp; <a href="backtest.html">시그널 백테스트 · 내 종목 12개월 →</a>{wide_link}</div>
{extra}
</body></html>"""

os.makedirs("docs", exist_ok=True)
# 머니파이 분석 탭용 시장 자료(브라우저에서 직접 못 받는 신용잔고·고객예탁금) — docs/market.json
def _market_json():
    def ser(v):
        if not v:
            return None
        return {k: (f"{x:%Y-%m-%d}" if hasattr(x, "strftime") else x) for k, x in v.items() if k != "spark"}
    with open("docs/market.json", "w", encoding="utf-8") as f:
        json.dump({"time": stamp, "credit": ser(snap.get("credit")), "credit_ks": ser(snap.get("credit_ks")),
                   "credit_top": m.CREDIT_TOP, "pbr_link": m.PBR_LINK}, f, ensure_ascii=False)
guard("시장 자료 내보내기", _market_json)
# 신호 기록: 오늘 칸을 history/에 저장(daily.yml이 커밋) + 지난 기록으로 docs/history.html
import history
guard("신호 기록 저장", lambda: history.save(history.record(results, msig, "us" if IS_US else "kr")))
guard("신호 기록 페이지", history.build, results)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
print("docs/index.html 생성 완료")

# ---- 텔레그램 전송 (선택) ----
token = os.environ.get("TELEGRAM_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")


def action_text():
    """텔레그램 첫 메시지: 지금 할 일만. 국장 리포트 = 국내 종목, 미장 리포트 = 해외 종목(같은 신호를 두 번 보내지 않게)"""
    mine = [r for r in results if m.is_kr(r["code"]) != IS_US]
    L = [f"[{'미장' if IS_US else '국장'} {kst:%m/%d %H:%M}] 할 일"]
    price = (lambda r: f"${r['close']:,.2f}") if IS_US else (lambda r: f"{r['close']:,.0f}원")
    any_ = False
    for key, nm, _ in m.REVIEW:
        if key not in ("sell",) + m.BUY_KEYS:
            continue
        hit = [r for r in mine if (r.get("review") or {}).get(key)]
        if not hit:
            continue
        any_ = True
        L.append(f"\n{nm} {len(hit)}")
        for r in hit:
            extra = f" · LOC {r['close'] * 1.08:,.2f}" if IS_US and key != "sell" else ""
            L.append(f"· {r['name']} — 종가 {price(r)}{extra}")
    if not any_:
        L.append("\n오늘 매수·매도 신호 없음")
    else:
        L.append("\n→ " + ("오늘 밤 LOC 예약 (지정가 = 종가 +8%), 매도는 다음 날 종가" if IS_US
                            else "시간외 종가로 15:40 전 주문 (16:00까지 체결)"))
    cau = [r["name"] for r in mine if (r.get("review") or {}).get("caution")]
    if cau:
        L.append("\n매수보류(보유 유지·추가 매수 없음): " + ", ".join(cau))
    L.append("\n리포트: https://kangsaem.github.io/moneypier/")
    return "\n".join(L)


def tg_send(text):
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


if token and chat_id:
    tg_send(guard("텔레그램 할 일", action_text, default=f"{TITLE} {stamp} (할 일 요약 실패)"))   # 1) 지금 할 일
    tg_send(f"{TITLE} {stamp}\n\n{short_report}")                                                # 2) 전체 요약(예전과 같음)
