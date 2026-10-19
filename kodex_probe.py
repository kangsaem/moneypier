"""KODEX AI전력핵심설비 10-16 매수·눌림 → 10-19 신호 없음 이유(2026-10-20 사용자 질문)"""
import market_report as m
df = m.load_prices("487240")
df = df[df["Close"].notna()]
for d in ("2026-10-15", "2026-10-16", "2026-10-19"):
    x = df[df.index <= d]
    r = m.analyze_df(x, "487240", "KODEX AI전력핵심설비", light=True)
    s = r["sig"]
    print(d, f"종가 {float(x['Close'].iloc[-1]):,.0f} RSI {r['rsi']:.1f}", "| 싼 조건:", s.get("buy_c"), "| 반등 신호:", s.get("buy_t"),
          "| 칸:", list((m.review(r) or {}).keys()) if hasattr(m, "review") else "")
