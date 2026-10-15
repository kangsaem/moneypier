"""한 종목의 최근 며칠 V1 판정 풀어 보기(2026-10-15) — 환경변수 SYMBOL, DAYS(기본 5) → results/sig_trace.txt"""
import os, market_report as m
sym, days = os.environ.get("SYMBOL", "XOM"), int(os.environ.get("DAYS", "5"))
df = m.load_prices(sym)
bench = m.bench_close(sym)
out = []
for k in range(days, 0, -1):
    d = df.iloc[:len(df) - k + 1]
    r = m.analyze_df(d, sym, sym)
    r["rs60"] = m.rel_strength(d["Close"].dropna(), bench.loc[:d.index[-1]] if bench is not None else None)
    rv = m.review(r); keys = m.display_keys(rv); sg = r["sig"]
    ch = r["chart"]; cl, m20 = ch["close"], ch["ma20"]
    gaps = [round((cl[i] / m20[i] - 1) * 100, 2) for i in range(-5, 0)]
    out.append(f"{d.index[-1]:%Y-%m-%d} 종가 {r['close']:.2f} RSI {r['rsi']:.1f}(과열 기준 {sg['rsi_hi']}) 이격도 {r['disp']['cur']:.1f}/최대 {r['disp']['max']:.1f}"
               f" 5일 {r['ret5']:+.1f}% rs60 {r['rs60'] and round(r['rs60'],1)}\n  칸 {keys} · 전체 {[k for k,v in rv.items() if v]}"
               f"\n  불타기 add={sg['add']} 조건 {sg['add_c']} 트리거 {sg['add_t']}\n  20일선 대비 최근 5일 {gaps} · 매도쪽 {sg['sell']} 과열 {sg['sell_c']} 꺾임 {sg['sell_t']}"
               f"\n  매수쪽 {sg['buy']} 싼 {sg['buy_c']} 반등 {sg['buy_t']}"
               f"\n  범위 { {k: m.range_text(m.valid_range(d, k)) for k in keys if k in m.RANGE_KEYS} }")
os.makedirs("results", exist_ok=True)
open("results/sig_trace.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))

# 가격 시험(선택): ASOF(종가일)까지 일봉 + 다음 거래일 종가 P → 추세 조건 하나하나
if os.environ.get("PRICES"):
    asof = os.environ.get("ASOF")
    d = df[df.index <= asof] if asof else df
    nd = m._next_biz_day(d.index[-1])
    L = [f"\n가격 시험: {d.index[-1]:%Y-%m-%d} 종가 {d['Close'].iloc[-1]:.2f} 기준, 다음 거래일({nd:%Y-%m-%d}) 종가 = P"]
    for p in [float(x) for x in os.environ["PRICES"].split(",")]:
        row = {c: (p if c in ("Close", "High", "Low", "Open") else float("nan")) for c in d.columns}
        import pandas as pd
        r = m.analyze_df(pd.concat([d, pd.DataFrame([row], index=pd.DatetimeIndex([nd]))]), sym, sym, light=True)
        ch, dd = r["chart"], r["disp"]
        L.append(f"P {p:.2f}: 주봉 정 {r['week']['ok'] and r['week']['above']} · 20일선 {ch['ma20'][-1]:.2f} 위 {p >= ch['ma20'][-1]} · 20일선 상승 {ch['ma20'][-1] > ch['ma20'][-6]}"
                 f" · 5개월선 {ch['m5'][-1]:.2f} 위 {ch['m5'][-1] is None or p >= ch['m5'][-1]} · RSI {r['rsi']:.1f} < {r['sig']['rsi_hi']} {r['rsi'] < r['sig']['rsi_hi']}"
                 f" · 50일 이격도 {dd['cur']:.2f} / 과거 최대 {dd['max']:.2f} = {dd['cur']/dd['max']*100:.1f}% < {m.ADD_DISP_MAX}% {dd['cur']/dd['max']*100 < m.ADD_DISP_MAX}"
                 f" · 50일선 {p/dd['cur']*100:.2f} · 과열 조건 {r['sig']['sell_c']} → 추세 유지 {m._trend_ok(r)}")
    print("\n".join(L))
