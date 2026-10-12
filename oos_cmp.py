"""다른 기간(최근 특별한 상승장 제외) 시험: V1 vs 같은 종목 적립 vs 지수(코스피·S&P500·나스닥) 적립·일시 보유 (2026-10-12)

OS_UNIVERSE = kospi | nasdaq — 그 기간 시작일 당시 목록(코스피 시가총액 상위 50 / 나스닥100 구성, 생존 편향 없음)
OS_WINDOW  = YYYY-MM-DD:YYYY-MM-DD (예 2016-01-01:2020-12-31). 시세를 끝 날짜에서 잘라 '그때'처럼 계산(미래 정보 없음).
규칙을 만들 때 쓰지 않은 구간이라 사실상 '안 쓴 기간' 시험. 다만 시세가 2014년 10월부터라 초반엔 이격도 과거 최대·최소 기록이 짧다.
비교(원금 100): V1 최종 규칙(held=(150,10,2), 손절 8%, 현금에서 시작) / 같은 종목 적립(매달 1·5·10·15·20번째 거래일, 시세 있는 종목에 균등)
 / 같은 종목 균등 일시 보유 / 지수 3개 적립·일시 보유
결과: results/oos_<대상>_<시작연도>-<끝연도>.html·json
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

UNI = os.environ.get("OS_UNIVERSE", "kospi")
W0, W1 = os.environ.get("OS_WINDOW", "2016-01-01:2020-12-31").split(":")
os.environ["BT_UNIVERSE"] = UNI
os.environ.setdefault("BT_TOP", "50")
os.environ["BT_OUT"] = "results/_tmp_oos"
os.environ["BT_DAYS"] = "1260"            # 아래에서 실제 기간 길이로 다시 맞춤

import pandas as pd            # noqa: E402

import market_report as m      # noqa: E402

END = pd.Timestamp(W1)
_lp, _ic = m.load_prices, m._index_close


def _cut(x):
    return x[x.index <= END]


m.load_prices = lambda code: _cut(_lp(code))            # 끝 날짜 뒤 시세는 없는 것처럼
m._index_close = lambda *a, **k: _cut(_ic(*a, **k))

import backtest as b           # noqa: E402
import portfolio as pf         # noqa: E402

ks = m._index_close("KS11", "^KS11")
ref = ks if UNI == "kospi" else m._index_close("IXIC", "^IXIC")
b.BT_DAYS = int((ref.index >= pd.Timestamp(W0)).sum())   # 시작일 ~ 끝 날짜 거래일 수
NTH = (1, 5, 10, 15, 20)


def irr(flows, end_date, end_val):
    t0 = flows[0][0]
    yrs = lambda d: (d - t0).days / 365.25
    return pf._irr([(yrs(d), -a) for d, a in flows] + [(yrs(end_date), end_val)])


def dca(px, nth):
    dates = px.index
    months = pd.Series(range(len(dates)), index=dates).groupby([dates.year, dates.month]).apply(list)
    buy_i = set(lst[min(nth, len(lst)) - 1] for lst in months)
    amt = 1.0 / len(buy_i)
    sh = pd.Series(0.0, index=px.columns)
    vals, flows = [], []
    for i, d in enumerate(dates):
        if i in buy_i:
            row = px.iloc[i].dropna()
            row = row[row > 0]
            if len(row):
                sh[row.index] += amt / len(row) * (1 - pf.BUY_COST) / row
                flows.append((d, amt))
        vals.append(float((sh * px.iloc[i].fillna(0)).sum()) + 1.0 - sum(a for _, a in flows))
    v = pd.Series(vals, index=dates)
    endv = float((sh * px.iloc[-1].fillna(0)).sum())
    return {"roc": (v.iloc[-1] - 1) * 100, "irr": irr(flows, dates[-1], endv), "mdd": float(((v / v.cummax()) - 1).min() * 100)}


def lump(s):
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    g = s.iloc[-1] / s.iloc[0]
    return {"roc": (g - 1) * 100, "irr": (g ** (1 / yrs) - 1) * 100, "mdd": float(((s / s.cummax()) - 1).min() * 100)}


def main():
    t0 = time.time()
    tickers = b.load_universe()
    for t in tickers:
        b.bench_for(t["code"])
    res = dict(b.run_all(tickers))
    states, failed = {}, []
    for i, t in enumerate(tickers):
        r = res.get(i)
        if r and r[0] == "ok":
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
        else:
            failed.append(t["name"])
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index if d >= pd.Timestamp(W0)}))
    eq, trades, cash, open_n, _ = pf.simulate_alloc(states, dates, 0, 0, same_day=(UNI == "kospi"), held=(150, 10, 2))
    v1m = pf.metrics(eq, trades, cash, open_n)
    yrs = (dates[-1] - dates[0]).days / 365.25
    out = {"universe": UNI, "window": f"{dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}", "n": len(states), "failed": failed, "note": b.UNIVERSE_NOTE,
           "v1": {"roc": v1m["tot"], "irr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, "mdd": v1m["mdd"], "trades": v1m["n"], "cash": v1m["cash"]}}
    px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
    out["basket_dca"] = [dca(px, n) for n in NTH]
    eqw = (px / px.bfill().iloc[0]).mean(axis=1)
    out["basket_lump"] = lump(eqw)
    out["idx"] = {}
    for nm, fc, yc in (("코스피", "KS11", "^KS11"), ("S&P500", "US500", "^GSPC"), ("나스닥", "IXIC", "^IXIC")):
        s = m._index_close(fc, yc)
        s = s[(s.index >= dates[0]) & (s.index <= dates[-1])]
        out["idx"][nm] = {"dca": [dca(s.to_frame("i"), n) for n in NTH], "lump": lump(s)}
    # 연도별(참고)
    out["years"] = {}
    for y in sorted({d.year for d in dates}):
        a = eq[eq.index.year == y]
        prev = eq[eq.index.year < y]
        out["years"][str(y)] = {"V1": float((a.iloc[-1] / (prev.iloc[-1] if len(prev) else a.iloc[0]) - 1) * 100)}
        for nm, fc, yc in (("코스피", "KS11", "^KS11"), ("S&P500", "US500", "^GSPC"), ("나스닥", "IXIC", "^IXIC")):
            s = m._index_close(fc, yc)
            sa, sp = s[s.index.year == y], s[s.index.year < y]
            if len(sa) and len(sp):
                out["years"][str(y)][nm] = float((sa.iloc[-1] / sp.iloc[-1] - 1) * 100)
    out["secs"] = round(time.time() - t0)
    out["run"] = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    name = f"oos_{UNI}_{W0[:4]}-{W1[:4]}"
    os.makedirs("results", exist_ok=True)
    with open(f"results/{name}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0, default=str)
    f = lambda v: "-" if v is None else f"{v:+.1f}%"
    rr = lambda rs, k: f"{f(min(r[k] for r in rs if r[k] is not None))} ~ {f(max(r[k] for r in rs if r[k] is not None))}"
    rows = [("V1 (현금에서 시작)", f(out['v1']['roc']), f(out['v1']['irr']), f"{out['v1']['mdd']:.1f}%"),
            ("같은 종목 적립", rr(out['basket_dca'], 'roc'), rr(out['basket_dca'], 'irr'), rr(out['basket_dca'], 'mdd')),
            ("같은 종목 균등 일시 보유", f(out['basket_lump']['roc']), f(out['basket_lump']['irr']), f"{out['basket_lump']['mdd']:.1f}%")]
    for nm, d in out["idx"].items():
        rows.append((f"{nm} 적립", rr(d['dca'], 'roc'), rr(d['dca'], 'irr'), rr(d['dca'], 'mdd')))
        rows.append((f"{nm} 일시 보유", f(d['lump']['roc']), f(d['lump']['irr']), f"{d['lump']['mdd']:.1f}%"))
    h = [f"<!doctype html><meta charset='utf-8'><title>{name}</title><style>body{{font:13px sans-serif;margin:16px}}td,th{{border:1px solid #ccc;padding:3px 7px;text-align:right}}td.l{{text-align:left}}table{{border-collapse:collapse}}</style>",
         f"<h1>다른 기간 시험 · {UNI} · {out['window']}</h1><p>{out['n']}종목 · {out['note']} · 실패 {len(failed)} · 실행 {out['run']}</p>",
         "<table><tr><th class='l'>방식</th><th>넣은 돈 대비</th><th>연 환산</th><th>최대 하락</th></tr>" +
         "".join(f"<tr><td class='l'>{a}</td><td>{b_}</td><td>{c}</td><td>{d}</td></tr>" for a, b_, c, d in rows) + "</table>",
         "<h2>연도별</h2><table><tr><th>연도</th><th>V1</th><th>코스피</th><th>S&P500</th><th>나스닥</th></tr>" +
         "".join(f"<tr><td>{y}</td>" + "".join(f"<td>{f(v.get(k))}</td>" for k in ("V1", "코스피", "S&P500", "나스닥")) + "</tr>" for y, v in out["years"].items()) + "</table>"]
    with open(f"results/{name}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    print(name, json.dumps({"v1": out["v1"], "basket_lump": out["basket_lump"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
