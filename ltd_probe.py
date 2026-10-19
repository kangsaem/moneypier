"""'장기 추세 아래' 칩(매수·눌림 범위 없음) 종목 확인(2026-10-20 사용자 질문: 10개월선보다 한참 아래인가)
종목마다: 마지막 종가 · 10개월선 · 거리 · 월봉 상태, 다음 거래일 종가를 +1~+30%로 올렸을 때
10개월선 위가 되는 첫 가격과 그때 켜지는 과열 조건(2개 이상이면 범위에서 빠짐)."""
import pandas as pd
import market_report as m

CODES = [("0167A0", "SOL AI반도체TOP2플러스"), ("487240", "KODEX AI전력핵심설비"), ("0162Z0", "RISE 삼성전자SK하이닉스채권혼합50")]

for code, name in CODES:
    try:
        df = m.load_prices(code)
        df = df[df["Close"].notna()]
        r = m.analyze_df(df, code, name, light=True)
        base, m10, mo = float(df["Close"].iloc[-1]), r["chart"]["m10"][-1], r["month"]
        if m10 is None:
            print(f"== {name} ({code}) {df.index[-1]:%Y-%m-%d} 종가 {base:,.0f} · 10개월선 없음 · 월봉 ok={mo.get('ok')} · RSI {r['rsi']:.1f} · "
                  f"상장 {df.index[0]:%Y-%m-%d} ({len(df)}거래일)")
            continue
        print(f"== {name} ({code}) {df.index[-1]:%Y-%m-%d} 종가 {base:,.0f} · 10개월선 {m10:,.0f} · 거리 {(base / m10 - 1) * 100:+.1f}% · "
              f"월봉 {'정배열' if mo.get('ok') and mo.get('above') else '역배열'} · RSI {r['rsi']:.1f} · 상장 {df.index[0]:%Y-%m-%d} ({len(df)}거래일)")
        print("   범위:", m.range_text(m.valid_range(df, "buy")))
        nd = m._next_biz_day(df.index[-1])
        for k in range(1, 31):
            p = base * (1 + k / 100)
            row = {c: (p if c in ("Close", "High", "Low", "Open") else float("nan")) for c in df.columns}
            d2 = pd.concat([df, pd.DataFrame([row], index=pd.DatetimeIndex([nd]))])
            r2 = m.analyze_df(d2, "vr", "vr", light=True)
            m10b, mo2 = r2["chart"]["m10"][-1], r2["month"]
            trend = (mo2["ok"] and mo2["above"]) or (m10b is not None and p > m10b)
            if trend or k in (5, 10, 20, 30):
                print(f"   +{k}%: 10개월선 위 {'O' if trend else 'X'} · 과열 {len(r2['sig']['sell_c'])}개 {r2['sig']['sell_c']}")
            if trend:
                break
    except Exception as e:
        print(f"== {name} ({code}) 실패: {type(e).__name__} {e}")
