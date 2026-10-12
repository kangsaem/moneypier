"""시세 이상치 점검 (2026-10-12)

다른 출처와 통째로 비교하는 대신, 리포트가 쓰는 시세(market_report.load_prices) 안에서 '틀린 값일 가능성이 큰 날'을 찾는다.
대상 = 내 종목 + 백테스트 종목(코스피 5년 시작일 기준 상위 50 · 나스닥100 5년 전 구성).
찾는 것
  튀었다 복귀 : 하루 ±10% 이상 움직였다가 다음 날 거의 제자리(이틀 합계 ±2% 이내)
  극단 등락   : 하루 ±30% 초과(국내 가격제한폭 밖) / 해외 ±40% 초과
  가격 모순   : 고가 < 종가, 저가 > 종가, 고가 < 저가
  같은 종가   : 5거래일 이상 종가가 똑같음(거래정지·자료 멈춤)
  거래량 0    : 상장 뒤 거래량 0 또는 빈 날(개수만)
  빠진 날     : 시장 지수에는 있는데 종목에는 없는 거래일(상장 뒤, 개수만)
그리고 이 종목의 기준값이 이상치 날에 걸려 있는지:
  50일 이격도 과거 최대·최소 날짜, 52주 고점·저점 날짜가 이상치 ±3거래일 안이면 '기준 오염 의심'.
의심 날은 독립 출처로 확인: 국내 = KRX 공식(FDR 'KRX:', 개별 종목만, 2년 단위) / 해외 = 나스닥 공식 사이트.
환경변수: DA_VERSION, DA_MEMO, DA_WORKERS
"""
import contextlib
import io
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

import market_report as m

OUT_DIR = os.environ.get("DA_OUT_DIR", "results")
VERSION = os.environ.get("DA_VERSION", "")
MEMO = os.environ.get("DA_MEMO", "")
WORKERS = int(os.environ.get("DA_WORKERS", str(os.cpu_count() or 2)))
SPIKE = 10.0          # 튀었다 복귀: 하루 이 % 이상
REVERT = 2.0          # 이틀 합계가 이 % 이내면 복귀로 봄
LIMIT_KR, LIMIT_US = 30.0, 40.0
FLAT_DAYS = 5
NEAR = 3              # 기준값 날짜가 이상치 ±NEAR 거래일 안
MAX_VERIFY = 4        # 종목당 독립 출처로 확인할 의심 날 수
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}


def universe():
    """내 종목 + 백테스트 종목(실패하면 그 묶음만 건너뜀)"""
    out, seen = [], set()

    def add(lst, grp):
        for t in lst:
            c = t["code"]
            k = c.split(".")[0]
            if k and k not in seen:
                seen.add(k)
                out.append((c, t.get("name") or c, grp))
    add([t for t in m.resolve_codes(m.load_tickers()) if t.get("code")], "내 종목")
    try:
        import backtest as b
        with contextlib.redirect_stdout(io.StringIO()):
            b.BT_DAYS, b.TOP = 1260, 50
            b.UNIVERSE = "kospi"
            add(b.load_universe(), "코스피50")
            b.UNIVERSE = "nasdaq"
            add(b.load_universe(), "나스닥100")
    except Exception as e:
        print("백테스트 종목 목록 실패:", e)
    return out


_CAL = {}


def calendar(kr):
    """시장 거래일(지수 날짜). 한 번 받아 두고 다시 씀(작업자에는 fork로 물려줌)"""
    if kr in _CAL:
        return _CAL[kr]
    _CAL[kr] = _calendar(kr)
    return _CAL[kr]


def _calendar(kr):
    try:
        s = m._index_close("KS11", "^KS11") if kr else m._index_close("US500", "^GSPC")
        return pd.DatetimeIndex(s.index).normalize()
    except Exception:
        return None


# ---------------------------------------------------------------- 독립 출처 확인
def verify_krx(code, day):
    import FinanceDataReader as fdr
    d = pd.Timestamp(day)
    with contextlib.redirect_stdout(io.StringIO()):
        df = fdr.DataReader(f"KRX:{code.split('.')[0]}", (d - timedelta(days=7)).strftime("%Y-%m-%d"),
                            (d + timedelta(days=7)).strftime("%Y-%m-%d"))
    df.index = pd.DatetimeIndex(df.index).normalize()
    return df["Close"] if "Close" in df else None


def verify_nasdaq(code, day):
    d = pd.Timestamp(day)
    for cls in ("stocks", "etf"):
        url = (f"https://api.nasdaq.com/api/quote/{code}/historical?assetclass={cls}"
               f"&fromdate={(d - timedelta(days=7)):%Y-%m-%d}&todate={(d + timedelta(days=7)):%Y-%m-%d}&limit=30")
        try:
            j = requests.get(url, headers=UA, timeout=20).json()
            rows = (((j or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
        except Exception:
            rows = []
        if rows:
            s = pd.Series({pd.Timestamp(datetime.strptime(r["date"], "%m/%d/%Y")):
                           float(str(r["close"]).replace("$", "").replace(",", "")) for r in rows})
            return s.sort_index()
    return None


def verify(code, kr, day, ours):
    """독립 출처 종가와 비교 → (상태, 그쪽 종가). 상태: 같음 / 다름 / 확인 불가"""
    try:
        s = verify_krx(code, day) if kr else verify_nasdaq(code, day)
    except Exception as e:
        return "확인 불가", f"{type(e).__name__}"[:40]
    if s is None or pd.Timestamp(day) not in s.index:
        return "확인 불가", "자료 없음"
    v = float(s.loc[pd.Timestamp(day)])
    # 해외 나스닥 사이트는 분할 반영 전 가격일 수 있어 정수배(분할) 차이는 따로 표시
    r = v / ours if ours else np.nan
    if abs(r - 1) <= 0.005:
        return "같음", v
    if not kr and r > 1.5 and abs(r - round(r)) < 0.02:
        return f"분할 {round(r)}배 차이", v
    return "다름", v


# ---------------------------------------------------------------- 점검
def check(arg):
    code, name, grp = arg
    kr = m.is_kr(code)
    out = {"code": code, "name": name, "group": grp, "market": "kr" if kr else "us", "issues": [], "counts": {}}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            df = m.load_prices(code)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:120]
        return out
    df = df[df["Close"].notna() & (df["Close"] > 0)].copy()
    df.index = pd.DatetimeIndex(df.index).normalize()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if len(df) < 60:
        out["error"] = f"자료 부족({len(df)}일)"
        return out
    c = df["Close"]
    out.update({"n": len(c), "first": str(c.index[0].date()), "last": str(c.index[-1].date())})
    r = c.pct_change() * 100
    idx = c.index
    flags = {}                                      # 날짜 → [사유]

    def flag(d, why):
        flags.setdefault(d, []).append(why)
    nxt = r.shift(-1)
    two = ((c.shift(-1) / c.shift(1)) - 1) * 100
    for d in idx[(r.abs() >= SPIKE) & (two.abs() <= REVERT) & (np.sign(r) != np.sign(nxt))]:
        flag(d, f"튀었다 복귀 {r[d]:+.1f}% → {nxt[d]:+.1f}%")
    lim = LIMIT_KR if kr else LIMIT_US
    for d in idx[r.abs() > lim]:
        flag(d, f"극단 등락 {r[d]:+.1f}%")
    if "High" in df and "Low" in df:
        h, lo = df["High"], df["Low"]
        bad = (h < c * 0.999) | (lo > c * 1.001) | (h < lo * 0.999)
        bad &= h.notna() & lo.notna() & (h > 0) & (lo > 0)
        out["counts"]["가격 모순"] = int(bad.sum())
        for d in idx[bad][-3:]:
            flag(d, f"가격 모순 (고 {h[d]:g} · 저 {lo[d]:g} · 종 {c[d]:g})")
    same = (c.diff() == 0).astype(int)
    run = same.groupby((same == 0).cumsum()).cumsum()
    ends = idx[(run >= FLAT_DAYS - 1) & (run.shift(-1, fill_value=0) < run)]
    out["counts"]["같은 종가 구간"] = int(len(ends))
    for d in ends[-3:]:
        flag(d, f"같은 종가 {int(run[d]) + 1}일 연속")
    if "Volume" in df:
        v = df["Volume"]
        out["counts"]["거래량 0"] = int((v.fillna(0) <= 0).sum())
    cal = calendar(kr)
    if cal is not None:
        cal = cal[(cal >= idx[0]) & (cal <= idx[-1])]
        miss = cal.difference(idx)
        out["counts"]["빠진 날"] = int(len(miss))
        out["missing_last"] = [str(d.date()) for d in miss[-5:]]
        out["counts"]["지수에 없는 날"] = int(len(idx.difference(cal)))

    # 기준값 날짜(이격도 최대·최소, 52주 고점·저점)
    disp = (c / c.rolling(50).mean() * 100).dropna()
    refs = {}
    if len(disp):
        refs["이격도 최대"] = (disp.idxmax(), float(disp.max()))
        refs["이격도 최소"] = (disp.idxmin(), float(disp.min()))
    hi = (df["High"] if "High" in df else c).tail(250)
    lo = (df["Low"] if "Low" in df else c).tail(250)
    refs["52주 고점"] = (hi.idxmax(), float(hi.max()))
    refs["52주 저점"] = (lo.idxmin(), float(lo.min()))
    pos = {d: i for i, d in enumerate(idx)}
    fpos = sorted(pos[d] for d in flags)
    out["refs"] = {k: [str(d.date()), round(v, 2)] for k, (d, v) in refs.items()}
    tainted = []
    for k, (d, v) in refs.items():
        p = pos.get(d)
        if p is not None and any(abs(p - q) <= NEAR for q in fpos):
            tainted.append(k)
    out["tainted"] = tainted

    # 독립 출처 확인: 기준값에 걸린 날 먼저, 그다음 최근 순
    days = sorted(flags, key=lambda d: (not any(abs(pos[d] - pos.get(refs[k][0], -99)) <= NEAR for k in tainted), -pos[d]))
    for i, d in enumerate(days):
        item = {"date": str(d.date()), "why": flags[d], "close": float(c[d]),
                "prev": float(c.iloc[pos[d] - 1]) if pos[d] else None,
                "next": float(c.iloc[pos[d] + 1]) if pos[d] + 1 < len(c) else None}
        if i < MAX_VERIFY:
            item["check"], item["other"] = verify(code, kr, d, float(c[d]))
            time.sleep(0.3)
        out["issues"].append(item)
    out["issues"].sort(key=lambda x: x["date"])
    return out


# ---------------------------------------------------------------- 화면
def write(results, secs):
    os.makedirs(OUT_DIR, exist_ok=True)
    now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    meta = {"kind": "data_anomaly", "version": VERSION, "memo": MEMO, "run": now, "secs": round(secs), "items": results}
    base = f"data_anomaly_{VERSION}" if VERSION else "data_anomaly"
    names = [base] + (["data_anomaly"] if VERSION else [])
    for nm in names:
        with open(os.path.join(OUT_DIR, nm + ".json"), "w", encoding="utf-8") as fp:
            json.dump(meta, fp, ensure_ascii=False, indent=1, default=str)

    ok = [x for x in results if "error" not in x]
    n_issue = sum(1 for x in ok if x["issues"])
    n_taint = sum(1 for x in ok if x["tainted"])
    checks = [i for x in ok for i in x["issues"] if "check" in i]
    cnt = {}
    for i in checks:
        k = "분할 차이" if i["check"].startswith("분할") else i["check"]
        cnt[k] = cnt.get(k, 0) + 1
    css = """body{font:13px/1.5 -apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;margin:16px;color:#222;background:#fff}
h1{font-size:18px}h2{font-size:15px;margin-top:26px}table{border-collapse:collapse;margin:8px 0;font-size:12px}
td,th{border:1px solid #ddd;padding:3px 6px;text-align:right;white-space:nowrap}th{background:#f4f4f4}td.l,th.l{text-align:left;white-space:normal}
.bad{background:#f9d0d0}.ok{background:#e6f6ea}.unk{background:#eee}.taint{color:#c0392b;font-weight:600}.note{color:#666;font-size:12px;max-width:900px}
@media (prefers-color-scheme:dark){body{background:#16171a;color:#ddd}th{background:#24262b}td,th{border-color:#333}.bad{background:#4a1e1e}.ok{background:#1d3a26}.unk{background:#2a2a2a}.note{color:#999}.taint{color:#ff8a7a}}"""
    h = [f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>시세 이상치 점검</title><style>{css}</style>",
         f"<h1>시세 이상치 점검</h1><p class='note'>실행 {now} KST · {secs/60:.0f}분 · 대상 {len(results)}종목(실패 {len(results) - len(ok)}) · "
         f"의심 날이 있는 종목 {n_issue} · <span class='taint'>기준 오염 의심</span>(이격도 최대·최소, 52주 고점·저점이 의심 날 ±{NEAR}거래일) {n_taint}종목 · "
         f"독립 출처 확인 {len(checks)}건: " + " · ".join(f"{k} {v}" for k, v in sorted(cnt.items())) + (f" · 메모: {MEMO}" if MEMO else "") + "</p>",
         f"<p class='note'>의심 날 기준: 하루 ±{SPIKE:.0f}% 이상 움직였다가 다음 날 제자리(이틀 합계 ±{REVERT:.0f}% 이내), 하루 ±{LIMIT_KR:.0f}%(국내)·±{LIMIT_US:.0f}%(해외) 초과, "
         f"고가·저가·종가 모순, {FLAT_DAYS}거래일 이상 같은 종가. 독립 출처 = 국내 KRX 공식(개별 종목만, ETF는 확인 불가), 해외 나스닥 공식 사이트. "
         "'같음'이면 실제로 일어난 움직임(데이터 문제 아님), '다름'이면 데이터 오류 가능성이 큼.</p>"]
    order = sorted(ok, key=lambda x: (not x["tainted"], not any(i.get("check") == "다름" for i in x["issues"]), not x["issues"], x["group"], x["code"]))
    h.append("<h2>종목별</h2><table><tr><th class='l'>종목</th><th class='l'>묶음</th><th>봉 수</th><th>의심 날</th><th class='l'>기준 오염 의심</th>"
             "<th>가격 모순</th><th>같은 종가 구간</th><th>거래량 0</th><th>빠진 날</th></tr>")
    for x in order:
        ct = x["counts"]
        h.append(f"<tr><td class='l'>{x['name']} <small>{x['code']}</small></td><td class='l'>{x['group']}</td><td>{x['n']}</td><td>{len(x['issues'])}</td>"
                 f"<td class='l taint'>{', '.join(x['tainted'])}</td><td>{ct.get('가격 모순', '-')}</td><td>{ct.get('같은 종가 구간', '-')}</td>"
                 f"<td>{ct.get('거래량 0', '-')}</td><td>{ct.get('빠진 날', '-')}</td></tr>")
    h.append("</table>")
    h.append("<h2>의심 날 목록</h2><table><tr><th class='l'>종목</th><th>날짜</th><th class='l'>사유</th><th>전날</th><th>그날</th><th>다음 날</th><th>독립 출처</th><th>그쪽 종가</th></tr>")
    for x in order:
        for i in x["issues"]:
            chk = i.get("check", "")
            cls = "ok" if chk == "같음" else "bad" if chk == "다름" else "unk" if chk else ""
            other = i.get("other", "")
            other = f"{other:,.2f}" if isinstance(other, (int, float)) else other
            fp = lambda v: "-" if v is None else f"{v:,.2f}"
            h.append(f"<tr><td class='l'>{x['name']} <small>{x['code']}</small></td><td>{i['date']}</td><td class='l'>{' / '.join(i['why'])}</td>"
                     f"<td>{fp(i['prev'])}</td><td>{fp(i['close'])}</td><td>{fp(i['next'])}</td><td class='{cls}'>{chk or '-'}</td><td>{other}</td></tr>")
    h.append("</table>")
    bad = [x for x in results if "error" in x]
    if bad:
        h.append("<h2>시세 실패</h2><p class='note'>" + " · ".join(f"{x['name']}({x['code']}): {x['error']}" for x in bad) + "</p>")
    html = "\n".join(h)
    for nm in names:
        with open(os.path.join(OUT_DIR, nm + ".html"), "w", encoding="utf-8") as fp:
            fp.write(html)


def main():
    t0 = time.time()
    todo = universe()
    print(f"대상 {len(todo)}종목, 작업자 {WORKERS}")
    calendar(True)
    calendar(False)
    if WORKERS > 1 and sys.platform != "win32":
        with mp.get_context("fork").Pool(WORKERS) as pool:
            res = pool.map(check, todo, chunksize=1)
    else:
        res = [check(x) for x in todo]
    for x in res:
        if x.get("error") or x.get("issues"):
            print(x["code"], x["name"], x.get("error", ""), len(x.get("issues", [])), x.get("tainted"))
    write(res, time.time() - t0)
    print(f"끝 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
