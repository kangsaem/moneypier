"""무작위 30종목: V1 vs 적립식 (2026-10-12, 리포트·기록과 무관한 별도 시험)

RC_MARKET = kospi | nasdaq | sp500(2026-10-13: 그때 S&P500 구성, fja05680/sp500 — 1996년부터), RC_SEED = 무작위 번호, 기간 5년(1260거래일).
RC_WINDOW = last(최근 5년) | random(최근 10년 안 무작위 시작 5년, 끝날 뒤 시세는 잘라서 안 씀), RC_N = 종목 수, RC_PREFIX = 결과 이름 앞부분.
종목 고르기(그 시작일 당시 목록에서 무작위 — 생존 편향 없음):
  코스피 = 시작일 시가총액 상위 300 중 무작위, 나스닥 = 시작일 나스닥100 구성 중 무작위. 시세 실패(상장폐지 등)는 빼고 30개를 채움.
비교(원금 100, 같은 종목 30개):
  V1 = 최종 규칙(portfolio.simulate_alloc held=(150,10,2), 손절 8%), 현금 100에서 시작
  적립 = 100을 개월 수로 나눠 매달 n번째 거래일(1·5·10·15·20·25, 그달 거래일이 모자라면 마지막 날) 종가에 시세 있는 종목 전부 균등. 팔지 않음
지표: 넣은 돈 대비 수익(판정 기준), 연 환산(IRR, 참고), 최대 하락(평가액 기준)
기간: 뽑은 날부터(2026-10-12 수정 — 전엔 코스피가 시세 전체 합집합이라 더 일찍 시작)
결과: results/rand_<시장>_s<번호>.html·json
"""
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

MK = os.environ.get("RC_MARKET", "kospi")
SEED = int(os.environ.get("RC_SEED", "1"))
N = int(os.environ.get("RC_N", "30"))
WIN = os.environ.get("RC_WINDOW", "last")          # last = 최근 5년 / random = 최근 10년 안에서 무작위 5년 / fixed = RC_START부터 5년(하락·횡보장 시험)
FIX_START = os.environ.get("RC_START", "")
PREFIX = os.environ.get("RC_PREFIX", "rand")       # 결과 파일 이름 앞부분(rand20 = 20종목 × 무작위 기간 시험)
END = None                                         # 무작위 기간의 끝날(그 뒤 시세는 안 씀)
os.environ["BT_UNIVERSE"] = "tickers"
os.environ["BT_DAYS"] = "1260"
os.environ["BT_OUT"] = "results/_tmp_rand"

import pandas as pd            # noqa: E402

import backtest as b           # noqa: E402
import market_report as m      # noqa: E402
import portfolio as pf         # noqa: E402

if WIN == "fixed":
    m.START = "2004-01-01"     # 과거 기간은 시세를 더 길게(신호 계산용 앞 1년 포함)
    if FIX_START and FIX_START[:4] < "2006":   # 1997~2005 시작(2026-10-13): 시작 2년 전부터(marcap은 1995년부터)
        m.START = f"{max(1995, int(FIX_START[:4]) - 2)}-01-01"
    b.BT_DAYS = 1450           # 신호를 끝날에서 1450거래일 거슬러 계산 → 5년(달력) 전체를 빠짐없이 덮음(앞부분은 시작일로 잘라냄)

NTH = (1, 5, 10, 15, 20, 25)
SP_RAW = "https://raw.githubusercontent.com/fja05680/sp500/master/"
SP_INFO = {}                                       # 티커 → 지수 편입·제외일(빠진 이유 분석용)


def sp500_at(day):
    """그날(또는 직전) S&P500 구성 티커 목록 + 티커별 지수 편입·제외일"""
    import io as _io
    import requests
    from urllib.parse import quote
    r = requests.get(SP_RAW + quote("S&P 500 Historical Components & Changes (Updated).csv"), timeout=120)
    r.raise_for_status()
    h = pd.read_csv(_io.StringIO(r.text))
    h["date"] = pd.to_datetime(h["date"])
    row = h[h["date"] <= day].iloc[-1]
    try:
        se = pd.read_csv(SP_RAW + "sp500_ticker_start_end.csv")
        for t, a, z in zip(se["ticker"], se["start_date"], se["end_date"]):
            SP_INFO.setdefault(t, []).append((str(a), "" if pd.isna(z) else str(z)))
    except Exception as e:
        print("편입·제외일 실패", e)
    return row["date"], sorted(set(row["tickers"].split(",")))


def sp_cover(df, start):
    """S&P 과거 구간: 시세가 시작일(10일 여유)부터 끝날(10일 여유)까지 이어져야 씀 → (통과, 이유)"""
    if df is None or not len(df):
        return False, "시세 없음"
    a, z = df.index[0], df.index[-1]
    if a > start + pd.Timedelta(days=10):
        return False, f"시세가 {a:%Y-%m-%d}부터(그때 회사와 다를 수 있음)"
    if z < END - pd.Timedelta(days=10):
        return False, f"기간 중 시세 끊김({z:%Y-%m-%d})"
    return True, ""


def marcap_prices(codes, y0, y1):
    """옛 국내 시세(2026-10-12): FDR이 오래된 기간(2007 등)을 못 주는 종목용. FinanceData/marcap 연도별 자료(상장폐지 종목 포함)에서
    시가·고가·저가·종가·거래량. 원래 가격이라 액면분할(상장주식수가 r배 + 종가가 약 1/r배인 날)을 찾아 이전 가격을 ÷r 보정"""
    import tempfile
    import requests
    want, parts = set(codes), []
    for y in range(y0, y1 + 1):
        try:
            r = requests.get(b.MARCAP_URL.format(y=y), timeout=180)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
                f.write(r.content)
                f.flush()
                d = pd.read_parquet(f.name, columns=["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Stocks"])
            d["Code"] = d["Code"].astype(str).str.zfill(6)
            parts.append(d[d["Code"].isin(want)])
        except Exception as e:
            print(f"marcap {y} 실패: {e}")
    if not parts:
        return {}
    a = pd.concat(parts)
    a["Date"] = pd.to_datetime(a["Date"])
    out = {}
    for c, g in a.groupby("Code"):
        g = g.sort_values("Date").set_index("Date")
        g = g[g["Close"] > 0]
        if len(g) < 2:
            continue
        sr = g["Stocks"] / g["Stocks"].shift(1)
        pr = g["Close"] / g["Close"].shift(1)
        adj = pd.Series(1.0, index=g.index)
        for t in g.index[((sr >= 1.5) | (sr <= 0.67)) & ((pr * sr - 1).abs() < 0.2)]:   # 분할·병합일
            adj[adj.index < t] *= 1.0 / sr[t]
        df = g[["Open", "High", "Low", "Close"]].mul(adj, axis=0)
        df["Volume"] = g["Volume"] / adj
        out[c] = df
    return out


def window(ix, rnd):
    """시작일(지수 거래일): last = 끝에서 1260번째 날 / random = 10년 전 이후 거래일 중 무작위(끝이 오늘을 넘지 않게)"""
    global END
    if WIN == "fixed":                  # 끝날 = 시작일 + 5년(달력) — 옛 지수 자료에 빠진 날이 있어도 기간이 밀리지 않게(2026-10-12 수정)
        k = ix.index.searchsorted(pd.Timestamp(FIX_START))
        END = ix.index[k] + pd.DateOffset(years=5) - pd.Timedelta(days=1)
        return ix.index[k]
    if WIN != "random":
        return ix.index[-1260]
    lo = ix.index.searchsorted(ix.index[-1] - pd.DateOffset(years=10))
    k = rnd.randint(lo, len(ix) - 1260)
    END = ix.index[k + 1259]
    return ix.index[k]


def pick():
    rnd = random.Random(f"{MK}-{SEED}")
    if MK == "kospi":
        ks = m._index_close("KS11", "^KS11")
        day = window(ks, rnd)
        d0, lst = b.kospi_top_at(day, 300)
        note = f"{d0:%Y-%m-%d} 코스피 시가총액 상위 300 중 무작위"
        start = d0
    elif MK == "sp500":
        ix = m._index_close("US500", "^GSPC")
        day = window(ix, rnd)
        d0, tk = sp500_at(day)
        lst = [{"code": t.replace(".", "-"), "name": t} for t in tk]
        note = f"{d0:%Y-%m-%d} S&P500 구성 중 무작위(빠진 종목은 다음 후보로 채움)"
        start = day
    else:
        from nasdaq_100_ticker_history import tickers_as_of
        ix = m._index_close("IXIC", "^IXIC")
        day = window(ix, rnd)
        tk = sorted(t for t in tickers_as_of(day.year, day.month, day.day) if t not in b.N100_DUP)
        lst = [{"code": b.N100_RENAME.get(t, t), "name": t} for t in tk]
        note = f"{day:%Y-%m-%d} 나스닥100 구성 중 무작위"
        start = day
    rnd.shuffle(lst)
    if MK == "sp500":
        return lst, note, pd.Timestamp(start)      # 앞에서부터 시세 확인하며 N개 채움(main)
    return lst[: N + (25 if WIN in ("random", "fixed") else 15)], note, pd.Timestamp(start)          # 시세 실패에 대비해 여유분


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
    t0 = flows[0][0]
    yrs = lambda d: (d - t0).days / 365.25
    irr = pf._irr([(yrs(d), -a) for d, a in flows] + [(yrs(dates[-1]), endv)])
    return {"nth": nth, "roc": (v.iloc[-1] - 1) * 100, "irr": irr, "mdd": float(((v / v.cummax()) - 1).min() * 100), "path": v.tolist()}


def main():
    t0 = time.time()
    cand, note, start = pick()
    if END is not None:                 # 무작위 기간: 끝날 뒤 시세를 잘라 그날까지로만 신호 계산(미래 정보 없음)
        _load = m.load_prices
        old = {}
        if MK == "kospi" and WIN == "fixed":   # 옛 기간: marcap 일별 시세(상폐 포함, 분할 보정)를 받아 둠
            old = marcap_prices([t["code"] for t in cand], start.year - 2, END.year)

        def _ld(code):
            try:
                df = _load(code)
            except Exception:
                df = pd.DataFrame()
            df = df[df.index <= END] if len(df) else df
            if code in old and len(old[code]) > 300:      # 코스피 과거 구간은 marcap 시세로 통일(FDR 옛 시세에 2014~2015년 보정 안 된 점프가 있어서, 2026-10-12)
                df = old[code][old[code].index <= END]
                df.attrs["src"] = "marcap"
            return df
        m.load_prices = _ld
    sp_fail = []
    if MK == "sp500":                   # 시세가 기간 전체를 덮는 종목만 앞에서부터 N개(빠진 후보는 이유와 함께 기록)
        ok = []
        for t in cand:
            if len(ok) >= N:
                break
            try:
                df = m.load_prices(t["code"])
            except Exception:
                df = None
            good, why = sp_cover(df, start)
            if good:
                ok.append(t)
            else:
                sp_fail.append({"t": t["name"], "why": why, "idx": SP_INFO.get(t["name"], []), "q": t["name"].endswith("Q")})
        cand = ok
    for t in cand:
        b.bench_for(t["code"])
    res = dict(b.run_all(cand))
    states, names, failed, reasons = {}, [], [], {}
    for i, t in enumerate(cand):
        r = res.get(i)
        if len(states) < N and not (r and r[0] == "ok"):
            failed.append(t["name"])                    # 시세 실패(상장폐지·합병 등) — 다음 후보로 채움
            reasons[t["name"]] = (r[1] if r else "결과 없음")[:160]   # 실패 이유(로그 없이 보려고, 2026-10-12)
        if r and r[0] == "ok" and len(states) < N:
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
            names.append(t["name"])
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    dates = dates[dates >= start]       # 뽑은 날부터(그 이전은 생존 편향) — 2026-10-12 수정
    if END is not None:
        dates = dates[dates <= END]
    eq, trades, cash, open_n, _ = pf.simulate_alloc(states, dates, 0, 0, same_day=(MK == "kospi"), held=(150, 10, 2))
    mt = pf.metrics(eq, trades, cash, open_n)
    yrs = (dates[-1] - dates[0]).days / 365.25
    v1 = {"roc": mt["tot"], "irr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, "mdd": mt["mdd"], "trades": mt["n"], "cash": mt["cash"]}
    px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
    runs = [dca(px, n) for n in NTH]
    hv = (px / px.bfill().iloc[0]).mean(axis=1) * (1 - pf.BUY_COST)   # 첫날 시세가 없는 종목은 첫 시세로           # 그냥 보유: 첫날 종목마다 같은 금액, 끝까지
    hold = {"roc": float((hv.iloc[-1] - 1) * 100), "mdd": float(((hv / hv.cummax()) - 1).min() * 100)}
    # 곡선(5거래일마다, 원금 1 기준): 나중에 'V1 반 + 보유 반' 같은 섞기를 다시 돌리지 않고 계산하려고(2026-10-12)
    e1 = (eq / eq.iloc[0]).reindex(dates).ffill()
    dv = pd.concat([pd.Series(r_["path"], index=dates) for r_ in runs], axis=1).mean(axis=1) if runs and "path" in runs[0] else None
    step = list(range(0, len(dates), 5)) + ([len(dates) - 1] if (len(dates) - 1) % 5 else [])
    curve = {"d": [f"{dates[i]:%Y-%m-%d}" for i in step], "v1": [round(float(e1.iloc[i]), 4) for i in step],
             "hold": [round(float(hv.iloc[i]), 4) for i in step]}
    if dv is not None:
        curve["dca"] = [round(float(dv.iloc[i]), 4) for i in step]
    out = {"market": MK, "seed": SEED, "n": len(states), "sp_fail": sp_fail, "sp_tried": len(sp_fail) + len(cand), "names": names, "failed": failed, "fail_reason": reasons, "note": note, "window": f"{dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}",
           "v1": v1, "dca": [{k: v for k, v in r_.items() if k != "path"} for r_ in runs], "hold": hold, "curve": curve, "secs": round(time.time() - t0),
           "run": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")}
    lo = lambda k: min(r[k] for r in runs if r[k] is not None)
    hi = lambda k: max(r[k] for r in runs if r[k] is not None)
    # 판정 = 넣은 돈 대비(둘 다 끝에 넣은 돈 100, 남은 현금 이자 없음). 연 환산은 V1(첫날 100 기준)과 적립(넣은 시점 기준 IRR)의 기준이 달라 참고만
    out["winner"] = "V1" if v1["roc"] > hi("roc") else "적립" if v1["roc"] < lo("roc") else "비슷(적립 범위 안)"
    os.makedirs("results", exist_ok=True)
    name = f"{PREFIX}_{MK}_{dates[0]:%Y%m}_s{SEED}" if WIN == "fixed" else f"{PREFIX}_{MK}_s{SEED}"
    with open(f"results/{name}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0)
    f = lambda v: "-" if v is None else f"{v:+.1f}%"
    h = [f"<!doctype html><meta charset='utf-8'><title>{name}</title><style>body{{font:13px sans-serif;margin:16px}}td,th{{border:1px solid #ccc;padding:3px 7px;text-align:right}}td.l{{text-align:left}}table{{border-collapse:collapse}}</style>",
         f"<h1>무작위 {out['n']}종목 · {MK} · 번호 {SEED} · {out['window']}</h1><p>{note} · 더 나은 쪽(넣은 돈 대비): <b>{out['winner']}</b> · 연 환산은 계산 기준이 달라 참고만 · 매수 0.05%, 매도 0.25%(수수료·거래세 대략치)를 반영. 배당·배당세·해외 주식 양도세·환율은 반영 제외</p>",
         "<table><tr><th class='l'>방식</th><th>넣은 돈 대비</th><th>연 환산</th><th>최대 하락</th></tr>"
         f"<tr><td class='l'>V1 (현금 100에서 시작)</td><td>{f(v1['roc'])}</td><td>{f(v1['irr'])}</td><td>{v1['mdd']:.1f}%</td></tr>"
         f"<tr><td class='l'>그냥 보유 (첫날 같은 금액)</td><td>{f(hold['roc'])}</td><td>-</td><td>{hold['mdd']:.1f}%</td></tr>"
         + "".join(f"<tr><td class='l'>적립 매달 {r['nth']}번째 거래일</td><td>{f(r['roc'])}</td><td>{f(r['irr'])}</td><td>{r['mdd']:.1f}%</td></tr>" for r in runs)
         + "</table><p>종목: " + ", ".join(names) + "</p>"
         + (f"<p>시세 실패로 빠진 후보 {len(failed)}개: " + ", ".join(failed) + "</p>" if failed else "")]
    with open(f"results/{name}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    print(name, out["winner"], json.dumps(v1, ensure_ascii=False), f"적립 {lo('roc'):+.1f}~{hi('roc'):+.1f}%")


if __name__ == "__main__":
    main()
