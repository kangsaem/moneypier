"""
백테스트: 지금 tickers.json 종목으로 최근 12개월(BT_DAYS 거래일) 동안 날마다 시그널을 다시 계산해,
신호가 '처음 뜬 날' 종가에 샀다고 보고 5·20·60거래일 뒤 수익률을 집계한다.
- 각 날짜는 그날까지의 데이터만 사용(이격도 과거 최대·최소, 52주 고점 등 모두 그 시점 기준) → 미래 데이터 누수 없음
- 결과: docs/backtest.html (요약 표 + 신호 목록), docs/backtest.csv (신호별 기록)
사용: python backtest.py   (build_page.py 다음에 실행. docs/ 폴더에 같이 저장됨)
넓은 검증: BT_UNIVERSE=kospi BT_TOP=50 BT_DAYS=1260 BT_OUT=results/backtest_wide python backtest.py
  (코스피 시가총액 상위 50개 · 최근 약 5년. backtest_wide.yml이 수동 실행으로 돌림)
"""
import html
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import market_report as m

BT_DAYS = int(os.environ.get("BT_DAYS", 252))         # 백테스트 기간(거래일). 252 ≈ 12개월, 1260 ≈ 5년
UNIVERSE = os.environ.get("BT_UNIVERSE", "tickers")    # tickers = 내 종목 / kospi = 코스피 시가총액 상위
TOP = int(os.environ.get("BT_TOP", 50))               # kospi일 때 몇 개
OUT = os.environ.get("BT_OUT", "docs/backtest")       # 결과 파일 경로(확장자 제외) → .html, .csv
HORIZONS = (5, 20, 60) # 신호 뒤 수익률을 볼 기간(거래일)
MIN_N = 5              # 이보다 적으면 '표본 부족'
COOLDOWN = 5           # 신호가 꺼진 뒤 이 거래일 수 이상 지나야 다시 '처음 뜬 날'로 셈(깜빡이는 신호 중복 방지)
NEUTRAL = {5: 0.5, 20: 1.0, 60: 2.0}   # 기준선과 차이가 이 %p 미만이면 '차이 없음'

# 분류: (이름, 기대 방향) — up = 신호 뒤 오르면 맞음, down = 내리거나 덜 오르면 맞음
CATEGORIES = [(nm, "down" if tone == "sell" or nm == "하락진행" else "up") for nm, _, _, tone in m.SIGNAL_GROUPS] + [
    ("골든X3", "up"), ("골든X2", "up"), ("데드X3", "down"), ("데드X2", "down"), ("과열", "down")]


# ---- 비교용 '새 규칙 후보' (리포트 규칙은 바꾸지 않고 백테스트에서만 나란히 계산)
SHORT_WIN = {"day": 3, "week": 2, "month": 1}   # 골든·데드 '최근' 기간을 짧게: 일 3거래일 · 주 2주 · 월 이번 달
VARIANTS = [("골든X3·짧게", "up"), ("골든X2·짧게", "up"), ("데드X3·짧게", "down"), ("데드X2·짧게", "down"),
            ("매수·추세O", "up"), ("매수·추세X", "up"),
            ("불타기·거래량↑", "up"), ("불타기·상대강도↑", "up"),
            ("익절검토·거래량폭증", "down"), ("과열·거래량폭증", "down"),
            ("매도·상대강도↓", "down"), ("비중축소·상대강도↓", "down"),
            ("골든X2·거래량↑", "up"), ("데드X2·거래량↑", "down")]
VOL_UP = 1.5       # 거래량 증가 = 20일 평균의 1.5배 이상
VOL_SPIKE = 2.5    # 거래량 폭증 = 20일 평균의 2.5배 이상
RS_DAYS = 60       # 상대강도 = 최근 60거래일 수익률 - 같은 기간 시장(코스피, 미국 종목은 S&P500) 수익률
REGIME_MA = 200    # 시장 국면 = 시장 지수가 200일선 위면 상승기, 아래면 하락기
# 비교 표: (제목, 설명, [현재 분류, 후보 분류들])
COMPARE = [
    ("골든크로스 기간", "현재 일 5거래일·주 4주·월 2개월 → 짧게 일 3거래일·주 2주·월 이번 달",
     ["골든X3", "골든X3·짧게", "골든X2", "골든X2·짧게"]),
    ("데드크로스 기간", "같은 방식", ["데드X3", "데드X3·짧게", "데드X2", "데드X2·짧게"]),
    ("매수 + 장기 추세", "추세O = 월봉 정배열 또는 종가가 10월선 위 / 추세X = 그 반대(지금 매수에서 빼고 반등대기로 보낼 후보)",
     ["매수", "매수·추세O", "매수·추세X"]),
    ("불타기 보완", f"거래량↑ = 반등한 날 거래량이 20일 평균의 {VOL_UP}배 이상 / 상대강도↑ = 최근 {RS_DAYS}거래일 수익률이 시장보다 높음",
     ["불타기", "불타기·거래량↑", "불타기·상대강도↑"]),
    ("익절검토·과열 보완", f"거래량폭증 = 최근 5일 중 거래량이 20일 평균의 {VOL_SPIKE}배 이상인 날이 있음(매수세 소진 신호)",
     ["익절검토", "익절검토·거래량폭증", "과열", "과열·거래량폭증"]),
    ("매도·비중축소 보완", f"상대강도↓ = 최근 {RS_DAYS}거래일 수익률이 시장보다 낮음",
     ["매도", "매도·상대강도↓", "비중축소", "비중축소·상대강도↓"]),
    ("골든·데드X2 보완", f"거래량↑ = 최근 5일 중 거래량이 20일 평균의 {VOL_UP}배 이상인 날이 있음",
     ["골든X2", "골든X2·거래량↑", "데드X2", "데드X2·거래량↑"]),
]
REGIMES = ("상승기", "하락기")


def categories_of(r, feat=None):
    """그날 이 종목에 켜진 분류 이름 집합 (현재 규칙 + 비교용 후보). feat = 그날의 거래량 비율·상대강도"""
    on = {nm for nm, _ in m.sig_groups(r)}
    for golden, nm in ((True, "골든"), (False, "데드")):
        k = m.cross_count(r, golden)
        if k >= 2:
            on.add(f"{nm}X{k}")
        k2 = m.cross_count(r, golden, SHORT_WIN)
        if k2 >= 2:
            on.add(f"{nm}X{k2}·짧게")
    if len(r["sig"]["sell_c"]) >= 2:
        on.add("과열")
    if "매수" in on:
        on.add("매수·추세O" if m.long_up(r) else "매수·추세X")
    f = feat or {}
    vr, vr5, rs = f.get("vr"), f.get("vr5"), f.get("rs")
    if vr is not None:
        if "불타기" in on and vr >= VOL_UP:
            on.add("불타기·거래량↑")
        for nm in ("익절검토", "과열"):
            if nm in on and vr5 is not None and vr5 >= VOL_SPIKE:
                on.add(f"{nm}·거래량폭증")
        for nm in ("골든X2", "데드X2"):
            if nm in on and vr5 is not None and vr5 >= VOL_UP:
                on.add(f"{nm}·거래량↑")
    if rs is not None:
        if "불타기" in on and rs > 0:
            on.add("불타기·상대강도↑")
        for nm in ("매도", "비중축소"):
            if nm in on and rs < 0:
                on.add(f"{nm}·상대강도↓")
    return on


_bench = {}


def bench_for(code):
    """시장 지수 종가(국내 6자리 코드 = 코스피, 그 외 = S&P500). 실패하면 None"""
    key = "kr" if code[:6].isdigit() else "us"
    if key not in _bench:
        try:
            _bench[key] = m._index_close("KS11", "^KS11") if key == "kr" else m._index_close("US500", "^GSPC")
        except Exception as e:
            print(f"시장 지수({key}) 실패: {e}")
            _bench[key] = None
    return _bench[key]


def run_stock(code, name):
    """한 종목: 날짜별 신호 재계산 → (신호 기록 목록, 기준선용 날짜별 수익률 목록)"""
    df = m.load_prices(code)
    df = df[df["Close"].notna()]
    c = df["Close"]
    n = len(c)
    if n < 300:
        raise ValueError(f"자료 부족({n}일)")
    start = max(260, n - BT_DAYS)
    fwd = {h: (c.shift(-h) / c - 1) * 100 for h in HORIZONS}     # 미래 수익률(결과 측정용으로만 사용)
    # 그날까지의 데이터만 쓰는 보조 지표: 거래량 비율, 상대강도, 시장 국면
    vr = vr5 = rs = reg = None
    if "Volume" in df and df["Volume"].fillna(0).sum() > 0:
        v = df["Volume"].replace(0, float("nan"))
        vr = v / v.shift(1).rolling(20, min_periods=10).mean()
        vr5 = vr.rolling(5, min_periods=1).max()
    b = bench_for(code)
    if b is not None and len(b) > REGIME_MA:
        b_al = b.reindex(c.index, method="ffill")
        rs = (c / c.shift(RS_DAYS) - 1) * 100 - (b_al / b_al.shift(RS_DAYS) - 1) * 100
        up = (b >= b.rolling(REGIME_MA).mean()).reindex(c.index, method="ffill")
        reg = up.map({True: "상승기", False: "하락기"})

    def val(sr, t):
        if sr is None:
            return None
        x = sr.iloc[t]
        return None if pd.isna(x) else (x if isinstance(x, str) else float(x))
    events, base = [], []
    last_on = {}                                                 # 분류별로 마지막으로 켜져 있던 날(t)
    for t in range(start - COOLDOWN, n):                         # 앞쪽 며칠은 '최근에 켜져 있었나' 판단용
        r = m.analyze_df(df.iloc[: t + 1], code, name, light=True)
        on = categories_of(r, {"vr": val(vr, t), "vr5": val(vr5, t), "rs": val(rs, t)})
        if t >= start:
            rets = {h: (None if pd.isna(fwd[h].iloc[t]) else float(fwd[h].iloc[t])) for h in HORIZONS}
            rg = val(reg, t)
            base.append({**rets, "reg": rg})
            for cat in sorted(on):
                if t - last_on.get(cat, -10 ** 9) > COOLDOWN:    # 최근 COOLDOWN일 동안 꺼져 있다가 새로 켜진 경우만
                    events.append({"date": c.index[t], "code": code, "name": name, "cat": cat,
                                   "close": float(c.iloc[t]), "rsi": r["rsi"], "reg": rg,
                                   **{f"r{h}": rets[h] for h in HORIZONS}})
        for cat in on:
            last_on[cat] = t
    return events, base


def load_universe():
    """백테스트 대상: 내 종목(tickers.json) 또는 코스피 시가총액 상위 TOP개"""
    if UNIVERSE == "kospi":
        lst = m.fdr.StockListing("KOSPI")
        cap = "Marcap" if "Marcap" in lst.columns else next(c for c in lst.columns if "cap" in c.lower())
        code = "Code" if "Code" in lst.columns else "Symbol"
        lst = lst.sort_values(cap, ascending=False).head(TOP)
        return [{"code": str(c), "name": n} for c, n in zip(lst[code], lst["Name"])]
    return [t for t in m.resolve_codes(m.load_tickers()) if t.get("code")]


def stats(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": sum(v) / len(v), "win": sum(1 for x in v if x > 0) / len(v) * 100,
            "med": float(pd.Series(v).median())}


def main():
    t0 = time.time()
    tickers = load_universe()
    events, base, failed = [], [], []
    for i, t in enumerate(tickers, 1):
        try:
            ev, bs = run_stock(t["code"], t["name"])
            events += ev
            base += bs
            print(f"[{i}/{len(tickers)}] {t['name']}: 신호 {len(ev)}건")
        except Exception as e:
            failed.append(f"{t['name']} ({t['code']}): {type(e).__name__}: {e}")
            print(f"[{i}/{len(tickers)}] {t['name']} 실패: {e}")

    B = {h: stats([b[h] for b in base]) for h in HORIZONS}
    rows = []
    for cat, way in CATEGORIES + VARIANTS:
        ev = [e for e in events if e["cat"] == cat]
        S, verdict = judge(ev, B, way)
        row = {"cat": cat, "way": way, "n": len(ev), "S": S, "verdict": verdict, "events": ev, "reg": {}}
        for rg in REGIMES:              # 시장 국면별(상승기/하락기) — 기준선도 같은 국면의 날들로
            evr = [e for e in ev if e.get("reg") == rg]
            Br = {h: stats([b[h] for b in base if b.get("reg") == rg]) for h in HORIZONS}
            Sr, vr_ = judge(evr, Br, way)
            row["reg"][rg] = {"n": len(evr), "S": Sr, "verdict": vr_, "B": Br}
        rows.append(row)
    reg_days = {rg: sum(1 for b in base if b.get("reg") == rg) for rg in REGIMES}

    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    if events:
        pd.DataFrame(events).assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d")).to_csv(
            OUT + ".csv", index=False, encoding="utf-8-sig")
    period = ""
    if events or base:
        ds = [e["date"] for e in events]
        period = f"{min(ds):%Y-%m-%d} ~ {max(ds):%Y-%m-%d}" if ds else ""
    write_html(rows, B, len(tickers) - len(failed), failed, period, time.time() - t0, reg_days)
    print(f"{OUT}.html 생성 완료 ({time.time() - t0:.0f}초, 신호 {len(events)}건)")


def judge(ev, B, way):
    """분류 신호들의 기간별 통계와 판정(기준선 B 대비)"""
    S = {h: stats([e[f"r{h}"] for e in ev]) for h in HORIZONS}
    verdict = {}
    for h in HORIZONS:
        if not S[h] or not B.get(h) or S[h]["n"] < MIN_N:
            verdict[h] = "표본 부족" if ev else "-"
        else:
            diff = S[h]["mean"] - B[h]["mean"]
            ok = diff > 0 if way == "up" else diff < 0
            word = "차이 없음" if abs(diff) < NEUTRAL.get(h, 0.5) else ("맞음" if ok else "틀림")
            verdict[h] = word + f" ({diff:+.1f}%p)"
    return S, verdict


def fmt(s, key, unit="%"):
    if not s:
        return "-"
    v = s[key]
    return f"{v:+.1f}{unit}" if key == "mean" else f"{v:.0f}%"


def ret_td(v):
    if v is None:
        return '<td class="mut">-</td>'
    return f'<td class="{"up" if v > 0 else "dn"}">{v:+.1f}%</td>'


def write_html(rows, B, n_stocks, failed, period, secs, reg_days=None):
    E = html.escape
    years = BT_DAYS / 252
    label = ("시그널 백테스트 · 내 종목" if UNIVERSE == "tickers" else f"시그널 백테스트 · 코스피 시총 상위 {TOP}") + \
        (f" · {years:.0f}년" if years >= 1.5 else f" · {BT_DAYS / 21:.0f}개월")
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    vcls = lambda v: "ok" if v.startswith("맞음") else "bad" if v.startswith("틀림") else "mut"
    byname = {r["cat"]: r for r in rows}

    def row_html(r, label=None, cls=""):
        cells = ""
        for h in HORIZONS:
            sh = r["S"][h]
            cells += (f'<td class="g">{fmt(sh, "mean")} <small>{sh["n"] if sh else 0}건</small></td><td>{fmt(sh, "win")}</td>'
                      f'<td class="{vcls(r["verdict"][h])}">{E(r["verdict"][h])}</td>')
        return (f'<tr class="{cls}"><td class="c"><b>{E(label or r["cat"])}</b> <small>{"▲" if r["way"] == "up" else "▼"}</small></td>'
                f'<td>{r["n"]}</td>{cells}</tr>')

    base_names = [c for c, _ in CATEGORIES]
    trs = "".join(row_html(byname[c]) for c in base_names)
    # 시장 국면별 표: 분류마다 상승기·하락기의 20·60일 판정(기준선도 같은 국면의 날들)
    reg_days = reg_days or {}
    tot = sum(reg_days.values()) or 1
    rtrs = ""
    for c in base_names:
        r = byname[c]
        cells = ""
        for rg in REGIMES:
            x = r["reg"].get(rg, {"n": 0, "verdict": {h: "-" for h in HORIZONS}})
            cells += f'<td class="g">{x["n"]}</td>' + "".join(
                f'<td class="{vcls(x["verdict"][h])}">{E(x["verdict"][h])}</td>' for h in (20, 60) if h in HORIZONS)
        rtrs += (f'<tr><td class="c"><b>{E(c)}</b> <small>{"▲" if r["way"] == "up" else "▼"}</small></td>{cells}</tr>')
    def rg_base(rg):
        Bx = next((byname[c]["reg"][rg]["B"] for c in base_names if rg in byname[c]["reg"]), {})
        return " · ".join(f'{h}일 평균 {fmt(Bx.get(h), "mean")}' for h in (20, 60) if h in HORIZONS)
    reg_note = " / ".join(f'{rg} {reg_days.get(rg, 0) / tot * 100:.0f}%의 날 (기준선 {rg_base(rg)})' for rg in REGIMES)
    rhead = "".join(f'<th colspan="3" class="g">{rg}</th>' for rg in REGIMES)
    rhead2 = "".join('<th class="g">신호</th><th>20일 판정</th><th>60일 판정</th>' for _ in REGIMES)
    ctrs = ""
    for title, desc, names in COMPARE:
        ctrs += (f'<tr class="grp"><td class="c" colspan="{2 + 3 * len(HORIZONS)}"><b>{E(title)}</b> '
                 f'<small>{E(desc)}</small></td></tr>')
        for nm in names:
            cur = nm in base_names
            ctrs += row_html(byname[nm], ("현재 · " if cur else "후보 · ") + nm, "cur" if cur else "alt")
    head1 = "".join(f'<th colspan="3" class="g">{h}일 뒤</th>' for h in HORIZONS)
    head2 = "".join('<th class="g">평균</th><th>플러스</th><th>판정</th>' for _ in HORIZONS)
    base = " / ".join(f'{h}일 뒤 평균 {fmt(B[h], "mean")} · 플러스 {fmt(B[h], "win")}' for h in HORIZONS)
    neutral = ", ".join(f"{h}일 ±{NEUTRAL[h]}%p" for h in HORIZONS)
    lists = ""
    for r in rows:
        if not r["events"] or r["cat"] not in base_names:
            continue
        li = "".join(
            f'<tr><td>{e["date"]:%y.%m.%d}</td><td>{E(e["name"])}</td><td>{m.fmt_price(e["close"])}</td>'
            f'<td>{e["rsi"]:.0f}</td>'
            + "".join(ret_td(e["r" + str(h)]) for h in HORIZONS) + "</tr>"
            for e in sorted(r["events"], key=lambda e: e["date"], reverse=True))
        lists += (f'<details><summary><b>{E(r["cat"])}</b> <small>{r["n"]}건</small></summary><div class="wrap">'
                  f'<table class="ev"><tr><th>날짜</th><th>종목</th><th>종가</th><th>RSI</th>'
                  + "".join(f"<th>{h}일 뒤</th>" for h in HORIZONS) + f'</tr>{li}</table></div></details>')
    fail = f'<pre class="warn">{E(chr(10).join(failed))}</pre>' if failed else ""
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{E(label)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --buy:#d92d20; --sell:#1d5fd1; --okbg:#d9f2ee; --ok:#0f766e; --badbg:#fdecea; --warnbg:#fef3c7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38; --buy:#ff6b5e; --sell:#6ea2ff; --okbg:#10302c; --ok:#4fd1c0; --badbg:#3a1d1a; --warnbg:#3a2e0e; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:980px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} .t {{ color:var(--mut); font-size:.8rem; margin-bottom:12px; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c, th.c {{ text-align:left; }} small {{ color:var(--mut); }}
td.ok {{ background:var(--okbg); color:var(--ok); font-weight:600; }} td.bad {{ background:var(--badbg); color:var(--buy); }} td.mut {{ color:var(--mut); }}
td.up {{ color:var(--buy); }} td.dn {{ color:var(--sell); }} .g {{ border-left:1px solid var(--line); }} th.g {{ text-align:center; }} tr.grp td {{ background:var(--bg); padding-top:10px; }} tr.cur td.c b {{ color:var(--mut); }}
.base {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px 12px; margin:12px 0; font-size:.85rem; }}
details {{ background:var(--card); border:1px solid var(--line); border-radius:10px; margin:6px 0; }} summary {{ cursor:pointer; padding:9px 12px; }}
table.ev td:nth-child(2), table.ev th:nth-child(2) {{ text-align:left; }}
.note {{ font-size:.78rem; color:var(--mut); }} pre.warn {{ background:var(--warnbg); padding:10px; border-radius:10px; white-space:pre-wrap; font-size:.78rem; }}
a {{ color:inherit; }}
</style></head><body>
<h1>{E(label)}</h1>
<div class="t">{E(period)} · 종목 {n_stocks}개 · 신호가 처음 뜬 날 종가 기준 · 계산 {kst:%Y-%m-%d %H:%M} KST ({secs:.0f}초) · <a href="./">리포트로</a></div>
<div class="base"><b>기준선</b> (같은 기간 아무 날이나 샀을 때) — {base}</div>
<div class="wrap"><table>
<tr><th class="c" rowspan="2">분류</th><th rowspan="2">신호</th>{head1}</tr>
<tr>{head2}</tr>
{trs}</table></div>
<p class="note">▲ = 신호 뒤 오르면 맞는 분류(매수·불타기·눌림진행·반등대기·골든), ▼ = 내리거나 덜 오르면 맞는 분류(매도·익절검토·비중축소·하락진행·데드·과열).
판정은 분류 평균과 기준선 평균의 차이(%p)로 봄({neutral} 안이면 '차이 없음'). {MIN_N}건 미만은 '표본 부족'. 플러스 = 수익률이 0보다 큰 비율.
같은 종목에서 같은 신호가 {COOLDOWN}거래일 안에 다시 뜨면 이어진 신호로 보고 한 번만 셈. 최근 신호는 아직 시간이 안 지나 긴 기간(20·60일) 결과가 없으므로, 기간별 건수가 다름.
한계: {"지금 보유·관심 종목만 대상(최근에 괜찮았던 종목 위주라 결과가 좋게 나오기 쉬움)" if UNIVERSE == "tickers" else "지금 시총 상위 종목 기준(과거에 상위였다 빠진 종목은 없음 — 결과가 다소 좋게 나오기 쉬움)"}, {"한 장세만 반영" if BT_DAYS < 500 else "여러 장세 포함"}, 거래비용 미반영.</p>
<h2 style="font-size:1rem">시장 국면별</h2>
<p class="note">시장(코스피, 미국 종목은 S&P500)이 {REGIME_MA}일선 위인 날 = 상승기, 아래인 날 = 하락기. 판정의 기준선도 같은 국면의 날들로 다시 계산.
{E(reg_note)}</p>
<div class="wrap"><table>
<tr><th class="c" rowspan="2">분류</th>{rhead}</tr>
<tr>{rhead2}</tr>
{rtrs}</table></div>
<h2 style="font-size:1rem">새 규칙 후보 비교</h2>
<p class="note">리포트 규칙은 바꾸지 않고, 같은 기간·같은 종목에서 후보 규칙을 나란히 계산한 결과. 후보가 '현재'보다 기준선 대비 차이가 크게(기대 방향으로) 나오고 건수도 충분하면 리포트에 반영할 만함.</p>
<div class="wrap"><table>
<tr><th class="c" rowspan="2">분류</th><th rowspan="2">신호</th>{head1}</tr>
<tr>{head2}</tr>
{ctrs}</table></div>
<h2 style="font-size:1rem">분류별 신호 목록</h2>{lists or '<div class="note">신호 없음</div>'}
{fail}
</body></html>"""
    with open(OUT + ".html", "w", encoding="utf-8") as f:
        f.write(page)


if __name__ == "__main__":
    main()
