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
