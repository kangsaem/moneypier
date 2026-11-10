"""신호·시세 날짜별 덤프 (2026-11-11) — 체결 시점(당일·다음 날)·손절·필터 연구용 원자료. 리포트·기록과 무관.

대상: SD_UNIVERSE (tickers | kospi | nasdaq), SD_DAYS 거래일(기본 2520 ≈ 10년), SD_PART "i/n"(종목을 n등분한 i번째).
날마다 그날까지 데이터만으로 V1 판정(market_report.analyze_df → review)을 다시 계산해 한 줄씩 저장.
결과: results/sigdump_<대상>_<i>.csv.gz
 열: date, code, o, h, l, c, v, keys(켜진 칸: sell·buyb·buy·up·hold·caution·wait·waitdn), sb(sig.buy), ss(sig.sell), nb(싼 조건 수),
     lu(장기 추세 상승), rsi, rs60, fh(52주 고점 대비), ret5, da·wa·ma(일·주·월 정배열), bn(바닥근접), bot(바닥 단계), reg(지수 200일선 위 = 1)
"""
import gzip
import os
import sys
import time
import multiprocessing as mp

UNI = os.environ.get("SD_UNIVERSE", "tickers")
DAYS = int(os.environ.get("SD_DAYS", 2520))
PART = os.environ.get("SD_PART", "1/1")
os.environ["BT_UNIVERSE"] = UNI
os.environ["BT_DAYS"] = str(DAYS)
os.environ.setdefault("BT_TOP", "100")
os.environ["BT_OUT"] = "results/_tmp_sigdump"

import pandas as pd           # noqa: E402

import backtest as b          # noqa: E402
import market_report as m     # noqa: E402


def one(arg):
    code, name = arg
    try:
        df = m.load_prices(code)
        df = df[df["Close"].notna()]
        c = df["Close"]
        n = len(c)
        if n < 300:
            return code, None, f"자료 부족({n})"
        start = max(260, n - DAYS)
        vr5 = rs = up = None
        if "Volume" in df and df["Volume"].fillna(0).sum() > 0:
            v = df["Volume"].replace(0, float("nan"))
            vr5 = (v / v.shift(1).rolling(20, min_periods=10).mean()).rolling(5, min_periods=1).max()
        bc = b.bench_for(code)
        if bc is not None and len(bc) > 200:
            b_al = bc.reindex(c.index, method="ffill")
            rs = (c / c.shift(60) - 1) * 100 - (b_al / b_al.shift(60) - 1) * 100
            up = (bc >= bc.rolling(200).mean()).reindex(c.index, method="ffill")
        g = lambda sr, t: None if sr is None or pd.isna(sr.iloc[t]) else float(sr.iloc[t])
        col = lambda k, t: (float(df[k].iloc[t]) if k in df and not pd.isna(df[k].iloc[t]) else "")
        rows = []
        for t in range(start, n):
            r = m.analyze_df(df.iloc[: t + 1], code, name, light=True)
            r["rs60"], r["vol5"] = g(rs, t), g(vr5, t)
            rv = m.review(r)
            keys = "|".join(k for k, _, _ in m.REVIEW if rv.get(k))
            sg = r["sig"]
            f = lambda x, d=2: "" if x is None else round(x, d)
            rows.append((c.index[t].strftime("%Y-%m-%d"), code, col("Open", t), col("High", t), col("Low", t), float(c.iloc[t]), col("Volume", t),
                         keys, sg.get("buy") or "", sg.get("sell") or "", len(sg.get("buy_c") or []), int(m.long_up(r)),
                         f(r["rsi"], 1), f(r["rs60"], 1), f(r["from_high"], 1), f(r["ret5"], 2),
                         int(bool(r["day"].get("ok") and r["day"].get("above"))), int(bool(r["week"].get("ok") and r["week"].get("above"))),
                         int(bool(r["month"].get("ok") and r["month"].get("above"))), int(bool(r.get("bottom_near"))), r.get("bottom") or 0,
                         "" if up is None or pd.isna(up.iloc[t]) else int(bool(up.iloc[t]))))
        return code, rows, ""
    except Exception as e:
        return code, None, f"{type(e).__name__}: {e}"


def main():
    t0 = time.time()
    tk = b.load_universe()
    i, k = (int(x) for x in PART.split("/"))
    tk = [t for j, t in enumerate(tk) if j % k == i - 1]
    print(UNI, PART, len(tk), "종목", b.UNIVERSE_NOTE)
    b.bench_for("005930"); b.bench_for("AAPL")
    args = [(t["code"], t["name"]) for t in tk]
    out = f"results/sigdump_{UNI}_{i}.csv.gz"
    os.makedirs("results", exist_ok=True)
    ok = fail = 0
    with gzip.open(out, "wt", encoding="utf-8") as fo:
        fo.write("date,code,o,h,l,c,v,keys,sb,ss,nb,lu,rsi,rs60,fh,ret5,da,wa,ma,bn,bot,reg\n")
        ctx = mp.get_context("fork")
        with ctx.Pool(min(os.cpu_count() or 1, len(args))) as pool:
            for code, rows, err in pool.imap_unordered(one, args):
                if rows is None:
                    fail += 1; print("실패", code, err); continue
                ok += 1
                for r in rows:
                    fo.write(",".join(str(x) for x in r) + "\n")
                print(f"{ok + fail}/{len(args)} {code} {len(rows)}일 {time.time() - t0:.0f}s", flush=True)
    print("끝", ok, "성공", fail, "실패", f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
