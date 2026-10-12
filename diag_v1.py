"""V1 계좌 점검 (2026-10-12) — 성과가 지나치게 좋아 보일 때 계산이 맞는지 확인하는 별도 시험(리포트·기록과 무관)

대상: DG_UNIVERSE (tickers | kospi | nasdaq), 5년. 계좌 = 최종 규칙(portfolio.simulate_alloc held=(150,10,2), 손절 8%).
보는 것
 1) 월별: V1 잔고·현금 비중·보유 종목 수 vs 균등 보유 — 투자 비중 100% 초과·현금 음수 같은 이상 여부
 2) 손익 기여: 종목별 합계·매매별 상위 — 소수 종목에 몰린 결과인지
 3) 미래 정보 점검: 모든 체결을 1·5거래일 늦춰 다시 계산 — 갑자기 무너지면 신호에 미래 정보가 섞였다는 뜻
 4) 연도별 수익: V1 vs 균등 vs 지수
결과: results/diag_v1_<대상>.html·json
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

UNI = os.environ.get("DG_UNIVERSE", "tickers")
os.environ["BT_UNIVERSE"] = UNI
os.environ["BT_DAYS"] = "1260"
os.environ.setdefault("BT_TOP", "50")
os.environ["BT_OUT"] = "results/_tmp_diag"

import numpy as np            # noqa: E402
import pandas as pd           # noqa: E402

import backtest as b          # noqa: E402
import market_report as m     # noqa: E402
import portfolio as pf        # noqa: E402

E = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")


def shifted(cats, k):
    return cats.shift(k).apply(lambda x: x if isinstance(x, (set, frozenset)) else set())


def run(states, dates, delay=0):
    sts = {}
    for c, s in states.items():
        cats = s["cats"]
        if UNI == "tickers" and not m.is_kr(c):
            cats = shifted(cats, 1)                       # 해외는 다음 날 체결(live.py 와 같은 방식)
        if delay:
            cats = shifted(cats, delay)
        sts[c] = {"close": s["close"], "cats": cats}
    same = UNI in ("kospi", "tickers")
    return pf.simulate_alloc(sts, dates, 0, 0, same_day=same, held=(150, 10, 2))


def main():
    t0 = time.time()
    tickers = b.load_universe()
    for t in tickers:
        b.bench_for(t["code"])
    res = dict(b.run_all(tickers))
    states, names = {}, {}
    for i, t in enumerate(tickers):
        r = res.get(i)
        if r and r[0] == "ok":
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
            names[t["code"]] = t["name"]
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    eq, trades, cash, open_n, rec = run(states, dates)
    px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
    eqw = (px / px.bfill().iloc[0]).mean(axis=1)
    idx = m._index_close("KS11", "^KS11") if UNI != "nasdaq" else m._index_close("IXIC", "^IXIC")
    idx = idx.reindex(dates, method="ffill")

    # 1) 월별 + 보유 종목 수(매매 기록으로 복원)
    held = pd.Series(0, index=dates, dtype=int)
    for tr in trades:
        held[(dates >= tr["in"]) & (dates < tr["out"])] += 1
    month = pd.DataFrame({"v1": eq / eq.iloc[0], "cash": cash * 100, "held": held, "eqw": eqw / eqw.iloc[0],
                          "idx": idx / idx.iloc[0]}).resample("ME").last()
    # 2) 손익 기여(끝난 매매 + 기말 보유는 계산 안 함 → 끝난 매매 기준)
    pnl = {}
    for tr in trades:
        pnl[tr["code"]] = pnl.get(tr["code"], 0.0) + (tr["back"] - tr["cost"])
    tot_pnl = sum(pnl.values())
    by_stock = sorted(pnl.items(), key=lambda kv: -kv[1])
    top_tr = sorted(trades, key=lambda t: -(t["back"] - t["cost"]))[:10]
    # 3) 늦춰 체결
    delays = {}
    for k in (1, 5):
        e2, t2, c2, o2, _ = run(states, dates, k)
        delays[k] = pf.metrics(e2, t2, c2, o2)
    base = pf.metrics(eq, trades, cash, open_n)
    # 4) 연도별
    yr = {}
    for nm, s in (("V1", eq), ("균등", eqw), ("지수", idx)):
        s = s.dropna()
        for y in sorted({d.year for d in s.index}):
            a = s[s.index.year == y]
            prev = s[s.index.year < y]
            st = prev.iloc[-1] if len(prev) else a.iloc[0]
            yr.setdefault(str(y), {})[nm] = float((a.iloc[-1] / st - 1) * 100)
    out = {"universe": UNI, "n": len(states), "run": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
           "secs": round(time.time() - t0), "start": f"{dates[0]:%Y-%m-%d}", "base": base, "delays": {str(k): v for k, v in delays.items()},
           "eqw_tot": float((eqw.iloc[-1] / eqw.iloc[0] - 1) * 100), "idx_tot": float((idx.iloc[-1] / idx.iloc[0] - 1) * 100),
           "cash_min": float(cash.min() * 100), "cash_first20": float(cash.iloc[:20].mean() * 100), "cash_mean": float(cash.mean() * 100),
           "held_max": int(held.max()), "years": yr, "n_trades": len(trades), "open_end": open_n,
           "pnl_total": tot_pnl * 100, "by_stock": [[names.get(c, c), round(v * 100, 2), round(v / tot_pnl * 100, 1) if tot_pnl else None] for c, v in by_stock],
           "top_trades": [[names.get(t["code"], t["code"]), f"{t['in']:%Y-%m-%d}", f"{t['out']:%Y-%m-%d}", round(t["ret"], 1), round((t["back"] - t["cost"]) * 100, 2), t["buys"], t["why"]] for t in top_tr],
           "monthly": [[f"{d:%Y-%m}", round(r.v1 * 100 - 100, 1), round(r.cash, 1), int(r.held), round(r.eqw * 100 - 100, 1), round(r.idx * 100 - 100, 1)] for d, r in month.iterrows()]}
    os.makedirs("results", exist_ok=True)
    with open(f"results/diag_v1_{UNI}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0)
    h = [f"<!doctype html><meta charset='utf-8'><title>V1 계좌 점검</title><style>body{{font:13px sans-serif;margin:16px}}td,th{{border:1px solid #ccc;padding:2px 6px;text-align:right}}table{{border-collapse:collapse;margin:8px 0}}</style>",
         f"<h1>V1 계좌 점검 · {UNI} · {out['start']}~ · {out['n']}종목</h1>",
         f"<p>V1 {base['tot']:+.1f}% (최대 하락 {base['mdd']:.1f}%) · 균등 {out['eqw_tot']:+.1f}% · 지수 {out['idx_tot']:+.1f}% · 1일 늦게 {delays[1]['tot']:+.1f}% · 5일 늦게 {delays[5]['tot']:+.1f}% · "
         f"현금 최저 {out['cash_min']:.1f}% · 처음 20일 평균 현금 {out['cash_first20']:.0f}% · 동시 보유 최대 {out['held_max']}종목</p>",
         "<h2>연도별</h2><table><tr><th>연도</th><th>V1</th><th>균등</th><th>지수</th></tr>" + "".join(f"<tr><td>{y}</td>" + "".join(f"<td>{v.get(k, 0):+.1f}%</td>" for k in ("V1", "균등", "지수")) + "</tr>" for y, v in yr.items()) + "</table>",
         "<h2>종목별 손익(끝난 매매)</h2><table><tr><th>종목</th><th>손익(원금 100 기준)</th><th>비중</th></tr>" + "".join(f"<tr><td>{E(a)}</td><td>{p:+.2f}</td><td>{s}%</td></tr>" for a, p, s in out["by_stock"]) + "</table>",
         "<h2>매매 상위 10</h2><table><tr><th>종목</th><th>산 날</th><th>판 날</th><th>수익률</th><th>손익</th><th>매수 횟수</th><th>이유</th></tr>" + "".join("<tr>" + "".join(f"<td>{E(x)}</td>" for x in row) + "</tr>" for row in out["top_trades"]) + "</table>",
         "<h2>월별</h2><table><tr><th>월</th><th>V1</th><th>현금%</th><th>보유 종목</th><th>균등</th><th>지수</th></tr>" + "".join("<tr>" + "".join(f"<td>{x}</td>" for x in row) + "</tr>" for row in out["monthly"]) + "</table>"]
    with open(f"results/diag_v1_{UNI}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    print(json.dumps({k: out[k] for k in ("base", "delays", "eqw_tot", "idx_tot", "cash_min", "cash_first20", "held_max")}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
