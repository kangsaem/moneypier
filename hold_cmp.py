"""그냥 보유 vs 보유 + 추적 손절(2026-10-12, 리포트·기록과 무관한 별도 시험)

rand20 결과(results/rand20_<시장>_s<번호>.json)의 같은 기간·같은 종목으로, 시세만 다시 받아 비교(신호 계산 없음 → 빠름).
- 보유     : 첫날 종목마다 같은 금액(원금 100 ÷ 종목 수)을 사서 끝까지(매수 비용 0.05%)
- 손절·현금 : 보유 + 종가가 산 뒤 최고 종가 × (1 − HC_TRAIL) 이하면 그날 종가 전량 매도(매도 비용 0.25%), 그 돈은 현금(이자 없음)
- 손절·재투입: 위와 같이 팔고, 판 돈을 그날 종가로 남은 보유 종목에 같은 금액씩 나눠 삼(남은 종목이 없으면 현금)
  다시 산 종목의 기준 최고가는 그대로 이어감(새로 산 몫도 같은 손절선)
쉬는 날·상장폐지 뒤에는 마지막 가격으로 평가. V1·적립은 rand20 결과에서 그대로 가져옴.
HC_MARKET = kospi | nasdaq, HC_SEEDS = 1-30, HC_TRAIL = 0.25, HC_OUT = 결과 이름(기본 hold_<시장>) → results/<이름>.json·html
"""
import json
import os
from statistics import mean, median

import pandas as pd

import backtest as b
import market_report as m

MK = os.environ.get("HC_MARKET", "kospi")
A, _, Z = os.environ.get("HC_SEEDS", "1-30").partition("-")
SEEDS = range(int(A), int(Z or A) + 1)
TRAIL = float(os.environ.get("HC_TRAIL", "0.25"))
OUT = os.environ.get("HC_OUT", "") or f"hold_{MK}"
BUY, SELL = 0.0005, 0.0025


_MC = {}


def _marcap_year(y):
    """연도별 시총 자료를 한 번만 받아 둠(번호마다 다시 받지 않게, 2026-10-13)"""
    if y not in _MC:
        import tempfile
        import requests
        r = requests.get(b.MARCAP_URL.format(y=y), timeout=180)
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
            f.write(r.content)
            f.flush()
            d = pd.read_parquet(f.name, columns=["Date", "Code", "Name", "Marcap", "Market"])
        d["Date"] = pd.to_datetime(d["Date"])
        _MC[y] = d
    return _MC[y]


def codes_for(d):
    """rand20 결과의 종목 이름 → 시세 코드(코스피는 시작일 당시 시총 상위 300의 이름으로 찾음 — b.kospi_top_at과 같은 목록)"""
    if MK == "nasdaq":
        return [b.N100_RENAME.get(n, n) for n in d["names"]]
    day = pd.Timestamp(d["window"][:10])
    by = {}
    for y in (day.year, day.year + 1):
        x = _marcap_year(y)
        x = x[(x["Date"] >= day) & (x["Market"] == "KOSPI")]
        if len(x):
            d0 = x["Date"].min()
            t = x[x["Date"] == d0].sort_values("Marcap", ascending=False).head(300)
            by = {n: str(c).zfill(6) for c, n in zip(t["Code"], t["Name"])}
            break
    return [by[n] for n in d["names"] if n in by]


def mdd(v):
    return float(((v / v.cummax()) - 1).min() * 100)


def run(px, mode):
    """px: 날짜 × 종목 종가(ffill). mode = hold | cash | redeploy → (최종 넣은 돈 대비 %, 최대 하락 %, 손절 횟수)"""
    n = px.shape[1]
    first = px.iloc[0]
    sh = (1.0 / n) * (1 - BUY) / first
    peak = first.copy()
    live = set(px.columns)
    cash, stops, vals = 0.0, 0, []
    for i in range(len(px)):
        p = px.iloc[i]
        if mode != "hold":
            peak = peak.where(peak >= p, p)
            hit = [c for c in live if p[c] <= peak[c] * (1 - TRAIL)]
            for c in hit:
                cash += sh[c] * p[c] * (1 - SELL)
                sh[c] = 0.0
                live.discard(c)
                stops += 1
            if hit and mode == "redeploy" and live and cash > 0:
                each = cash / len(live)
                for c in live:
                    sh[c] += each * (1 - BUY) / p[c]
                cash = 0.0
        vals.append(float((sh * p).sum()) + cash)
    v = pd.Series(vals, index=px.index)
    return float((v.iloc[-1] - 1) * 100), mdd(v), int(stops)


def main():
    rows = []
    for s in SEEDS:
        f = f"results/rand20_{MK}_s{s}.json"
        if not os.path.exists(f):
            continue
        d = json.load(open(f, encoding="utf-8"))
        t0, t1 = pd.Timestamp(d["window"][:10]), pd.Timestamp(d["window"][-10:])
        closes = {}
        for c in codes_for(d):
            try:
                df = m.load_prices(c)
                cl = df["Close"].dropna()
                cl = cl[cl.index <= t1]
                if len(cl) and cl.index[0] <= t0 + pd.Timedelta(days=10):
                    closes[c] = cl
            except Exception as e:
                print(s, c, "시세 실패", e)
        px = pd.DataFrame(closes).ffill()
        px = px[(px.index >= t0) & (px.index <= t1)].dropna(axis=1, how="any")
        r = {"seed": s, "window": d["window"], "n": int(px.shape[1]), "v1": d["v1"]["roc"], "v1m": d["v1"]["mdd"],
             "dca": mean(x["roc"] for x in d["dca"]), "dcam": mean(x["mdd"] for x in d["dca"])}
        for k in ("hold", "cash", "redeploy"):
            roc, dd, st = run(px, k)
            r[k], r[k + "m"], r[k + "s"] = roc, dd, st
        rows.append(r)
        print(f"{MK} s{s} {d['window']} n{r['n']} V1 {r['v1']:+.1f} 적립 {r['dca']:+.1f} 보유 {r['hold']:+.1f}({r['holdm']:.0f}) "
              f"손절현금 {r['cash']:+.1f}({r['cashm']:.0f}) 손절재투입 {r['redeploy']:+.1f}({r['redeploym']:.0f}) 손절 {r['cashs']}")
    if not rows:
        print("결과 없음")
        return
    keys = [("v1", "V1"), ("dca", "적립"), ("hold", "보유"), ("cash", "보유+손절·현금"), ("redeploy", "보유+손절·재투입")]
    agg = {k: {"mean": mean(r[k] for r in rows), "med": median(r[k] for r in rows), "mdd": mean(r[k + "m"] for r in rows),
               "v1_wins": int(sum(bool(r["v1"] > r[k]) for r in rows))} for k, _ in keys}
    out = {"market": MK, "trail": TRAIL, "n": len(rows), "rows": rows, "agg": agg}
    os.makedirs("results", exist_ok=True)
    with open(f"results/{OUT}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0)
    P = lambda v: f"<span class='{'up' if v > 0 else 'dn' if v < 0 else ''}'>{v:+.1f}%</span>"
    h = ["<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>hold_{MK}</title><style>body{{font:13px/1.5 -apple-system,'Noto Sans KR',sans-serif;margin:12px;background:#1E1F22;color:#E8ECF3}}"
         "table{border-collapse:collapse;margin:6px 0 14px}td,th{border-bottom:1px solid #3a3b40;padding:4px 7px;text-align:right;white-space:nowrap}"
         "th{color:#9A9BA1;font-weight:500}.l{text-align:left}.up{color:#FF4D5E}.dn{color:#3D8BFF}div{overflow-x:auto}p{color:#9A9BA1}</style>",
         f"<h1 style='font-size:16px'>그냥 보유 vs 보유 + 추적 손절 −{TRAIL * 100:.0f}% · {'코스피' if MK == 'kospi' else '나스닥'} {len(rows)}번</h1>",
         "<p>rand20 시험과 같은 기간·같은 20종목. 넣은 돈 대비 수익(원금 100, 이자 없음). 손절 = 산 뒤 최고 종가 대비 하락이 기준 이하인 날 종가에 전량. "
         "현금 = 판 돈을 그대로 둠, 재투입 = 판 돈을 남은 보유 종목에 같은 금액씩 다시 삼. 매수 0.05%, 매도 0.25%(수수료·거래세 대략치)를 반영. 배당·배당세·해외 주식 양도세·환율은 반영 제외.</p>",
         "<div><table><tr><th class='l'>방식</th><th>평균</th><th>중앙값</th><th>최대 하락 평균</th><th>V1이 더 높은 횟수</th></tr>"]
    for k, nm in keys:
        a = agg[k]
        h.append(f"<tr><td class='l'>{nm}</td><td>{P(a['mean'])}</td><td>{P(a['med'])}</td><td>{a['mdd']:.1f}%</td><td>{'-' if k == 'v1' else str(a['v1_wins']) + ' / ' + str(len(rows))}</td></tr>")
    h.append("</table></div><div><table><tr><th>번호</th><th class='l'>기간</th><th>종목</th>" + "".join(f"<th>{nm}</th>" for _, nm in keys) + "<th>손절 횟수</th></tr>")
    for r in sorted(rows, key=lambda x: x["window"]):
        h.append(f"<tr><td>{r['seed']}</td><td class='l'>{r['window']}</td><td>{r['n']}</td>" + "".join(f"<td>{P(r[k])}</td>" for k, _ in keys) + f"<td>{r['cashs']}</td></tr>")
    h.append("</table></div><p>한계: 같은 종목의 시세를 다시 받아 계산(rand20 때와 시세 출처 같음). 쉬는 날·상장폐지 뒤엔 마지막 가격. 손절 뒤 다시 사는 규칙 없음(재투입은 남은 종목으로만).</p>")
    with open(f"results/{OUT}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    for k, nm in keys:
        a = agg[k]
        print(f"{nm}: 평균 {a['mean']:+.1f} 중앙값 {a['med']:+.1f} 최대 하락 {a['mdd']:.1f} V1 우위 {a['v1_wins']}/{len(rows)}")


if __name__ == "__main__":
    main()
