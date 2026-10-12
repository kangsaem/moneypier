"""시세 출처 교차 검증 (2026-10-12)

리포트·백테스트가 쓰는 시세(market_report.load_prices = 국내 FinanceDataReader, 해외 FDR→yfinance)를
다른 출처와 날짜별로 맞대 본다. 기준(A) = 리포트 출처. 비교 출처:
  국내: 네이버 siseJson(머니파이가 쓰는 것) · 야후 원주가(.KS/.KQ, 분할만 반영) · FDR 'NAVER:' · FDR 'KRX:'
  해외: 야후 원주가(머니파이가 쓰는 것, 분할만 반영) · 야후 수정주가(배당까지 반영) · Stooq · FDR 'YAHOO:'
보는 것
  1) 종가: 최근 5년 공통 날짜의 차이(중앙값·99%·최대, 0.5% 넘는 날 비율), 5년 앞뒤 비율 변화(= 배당·분할 조정 차이)
  2) 거래량: 차이 중앙값, 5% 넘는 날 비율
  3) 날짜: 기준에만 있는 날 / 비교 출처에만 있는 날
  4) 신호 영향: 최근 252거래일 날마다 V1 칸(display_keys)을 출처별로 다시 계산 → 기준과 다른 날 수,
     매수·매도 신호 시작일 차이. 지수(상대강도용)는 모든 출처에 같은 값을 써서 종목 시세 차이만 본다.
환경변수: DC_VERSION(파일 이름 버전), DC_MEMO, DC_DAYS(신호 비교 기간, 기본 252), DC_WORKERS
"""
import io
import json
import os
import re
import sys
import time
import contextlib
import multiprocessing as mp
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

import market_report as m

OUT_DIR = os.environ.get("DC_OUT_DIR", "results")
VERSION = os.environ.get("DC_VERSION", "")
MEMO = os.environ.get("DC_MEMO", "")
SIG_DAYS = int(os.environ.get("DC_DAYS", "252"))
WORKERS = int(os.environ.get("DC_WORKERS", str(os.cpu_count() or 2)))
CMP_DAYS = 1260                  # 종가·거래량 비교 기간(최근 5년)
TOL_CLOSE = 0.5                  # 종가 차이 허용(%)
TOL_VOL = 5.0                    # 거래량 차이 허용(%)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

# 표본: 내 종목(국내·해외) + 분할·배당·코스닥을 시험할 대형주
KR_EXTRA = [("005930", "삼성전자(2018 분할)"), ("035420", "NAVER(2018 분할)"), ("033780", "KT&G(고배당)"),
            ("105560", "KB금융(배당)"), ("086520", "에코프로(코스닥·2024 분할)")]
US_EXTRA = [("AAPL", "애플(2020 분할)"), ("NVDA", "엔비디아(2021·2024 분할)"), ("AMZN", "아마존(2022 분할)"),
            ("GOOGL", "알파벳(2022 분할)"), ("AVGO", "브로드컴(2024 분할)"), ("WMT", "월마트(2024 분할)"),
            ("TSLA", "테슬라(2020·2022 분할)"), ("KO", "코카콜라(배당)"), ("XOM", "엑슨모빌(배당)"),
            ("SPY", "SPY(ETF)"), ("QQQ", "QQQ(ETF)"), ("TLT", "TLT(채권 ETF·월배당)")]


def kr_base(code):
    return code.split(".")[0]


def yahoo_sym(code):
    """국내 코드 → 야후 기호(.KS 먼저, 나중에 .KQ 시도)"""
    return code if "." in code or not m.is_kr(code) else code + ".KS"


def norm(df):
    """Close·Volume(+High·Low) 날짜 오름차순, 시간대 제거, 0·빈값 종가 제거"""
    if df is None or not len(df):
        return None
    df = df.copy()
    df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index = df.index.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cols = {c.lower(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for k in ("Close", "High", "Low", "Volume"):
        if k.lower() in cols:
            out[k] = pd.to_numeric(df[cols[k.lower()]], errors="coerce")
    out = out[out["Close"] > 0]
    return out if len(out) else None


# ---------------------------------------------------------------- 출처별 받기
def src_report(code):
    with contextlib.redirect_stdout(io.StringIO()):
        return norm(m.load_prices(code))


def src_naver(code):
    end = datetime.now() + timedelta(days=1)
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={kr_base(code)}&requestType=1"
           f"&startTime={m.START.replace('-', '')}&endTime={end:%Y%m%d}&timeframe=day")
    t = requests.get(url, headers=UA, timeout=30).text
    rows = re.findall(r'\[\s*["\'](\d{8})["\']\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)(?:\s*,\s*([-\d.]+))?', t)
    if not rows:
        raise ValueError("네이버 응답에 일봉 없음")
    df = pd.DataFrame([(pd.Timestamp(d), float(h), float(lw), float(c), float(v) if v else np.nan)
                       for d, o, h, lw, c, v in rows], columns=["Date", "High", "Low", "Close", "Volume"]).set_index("Date")
    return norm(df)


def _yf(sym, adjust):
    import yfinance as yf
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        df = yf.Ticker(sym).history(start=m.START, auto_adjust=adjust, actions=False)
    return norm(df)


def src_yahoo_raw(code):
    sym = yahoo_sym(code)
    df = _yf(sym, False)
    if df is None and sym.endswith(".KS"):
        df = _yf(sym[:-3] + ".KQ", False)
    if df is None:
        raise ValueError("야후 데이터 없음")
    return df


def src_yahoo_adj(code):
    df = _yf(yahoo_sym(code), True)
    if df is None:
        raise ValueError("야후 데이터 없음")
    return df


def src_stooq(code):
    url = f"https://stooq.com/q/d/l/?s={code.lower()}.us&i=d"
    t = requests.get(url, headers=UA, timeout=30).text
    if not t.startswith("Date"):
        raise ValueError("Stooq 응답 형식 다름: " + t[:60].replace("\n", " "))
    df = pd.read_csv(io.StringIO(t), parse_dates=["Date"]).set_index("Date")
    return norm(df[df.index >= m.START])


def _fdr(prefix, code):
    import FinanceDataReader as fdr
    with contextlib.redirect_stdout(io.StringIO()):
        df = fdr.DataReader(f"{prefix}:{kr_base(code) if prefix != 'YAHOO' else code}", m.START)
    df = norm(df)
    if df is None:
        raise ValueError("데이터 없음")
    return df


SOURCES_KR = [("naver", "네이버(머니파이)", src_naver), ("yahoo", "야후 원주가", src_yahoo_raw),
              ("fdr_naver", "FDR NAVER:", lambda c: _fdr("NAVER", c)), ("fdr_krx", "FDR KRX:", lambda c: _fdr("KRX", c))]
SOURCES_US = [("yahoo", "야후 원주가(머니파이)", src_yahoo_raw), ("yahoo_adj", "야후 수정주가(배당 반영)", src_yahoo_adj),
              ("stooq", "Stooq", src_stooq), ("fdr_yahoo", "FDR YAHOO:", lambda c: _fdr("YAHOO", c))]


def fetch(fn, code, tries=3):
    err = None
    for i in range(tries):
        try:
            df = fn(code)
            if df is not None and len(df) >= 30:
                return df, None
            err = "데이터 부족"
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:160]
        time.sleep(1.5 * (i + 1))
    return None, err


# ---------------------------------------------------------------- 비교
def compare(a, b):
    """기준 a와 비교 b: 최근 CMP_DAYS 기준일 안의 공통 날짜로 종가·거래량 차이"""
    a5 = a.iloc[-CMP_DAYS:]
    lo = a5.index[0]
    b5 = b[b.index >= lo]
    common = a5.index.intersection(b5.index)
    res = {"n_a": int(len(a5)), "n_b": int(len(b5)), "common": int(len(common)),
           "only_a": [str(d.date()) for d in a5.index.difference(b5.index)][-10:],
           "only_a_n": int(len(a5.index.difference(b5.index))),
           "only_b": [str(d.date()) for d in b5.index.difference(a5.index)][-10:],
           "only_b_n": int(len(b5.index.difference(a5.index)))}
    if len(common) < 30:
        return res
    ca, cb = a5.loc[common, "Close"], b5.loc[common, "Close"]
    d = (cb / ca - 1) * 100
    ad = d.abs()
    res.update({"c_med": float(ad.median()), "c_p99": float(ad.quantile(.99)), "c_max": float(ad.max()),
                "c_max_date": str(ad.idxmax().date()), "c_bad": float((ad > TOL_CLOSE).mean() * 100),
                "c_bad_n": int((ad > TOL_CLOSE).sum()),
                # 앞 20일·뒤 20일 비율 차이 = 기간 전체에 걸친 조정(배당·분할) 차이
                # 일간 등락률 차이: 수준은 달라도 날마다의 움직임이 같으면 조정(배당·분할 반영) 차이
                "r_bad_n": int(((cb.pct_change() - ca.pct_change()).abs() * 100 > TOL_CLOSE).sum()),
                "drift": float(((cb / ca).tail(20).median() / (cb / ca).head(20).median() - 1) * 100)})
    worst = ad.sort_values(ascending=False).head(5)
    res["c_worst"] = [[str(k.date()), round(float(ca[k]), 4), round(float(cb[k]), 4), round(float(d[k]), 2)] for k in worst.index]
    # 전체 기간(12년) 최대 차이 — 50일 이격도 과거 최대·최소에 영향
    call = a.index.intersection(b.index)
    if len(call):
        dd = ((b.loc[call, "Close"] / a.loc[call, "Close"] - 1) * 100).abs()
        res["c_max_all"] = float(dd.max())
        res["c_max_all_date"] = str(dd.idxmax().date())
        res["first_a"], res["first_b"] = str(a.index[0].date()), str(b.index[0].date())
    if "Volume" in a5 and "Volume" in b5:
        va, vb = a5.loc[common, "Volume"], b5.loc[common, "Volume"]
        ok = (va > 0) & (vb > 0)
        if ok.sum() >= 30:
            vd = ((vb[ok] / va[ok] - 1) * 100).abs()
            res.update({"v_med": float(vd.median()), "v_bad": float((vd > TOL_VOL).mean() * 100),
                        "v_ratio": float((vb[ok] / va[ok]).median())})
        res["v_zero_a"] = int((va.fillna(0) <= 0).sum())
        res["v_zero_b"] = int((vb.fillna(0) <= 0).sum())
    return res


def daily_keys(df, code, days):
    """최근 days거래일 날마다 V1 칸(display_keys)과 RSI·이격도 — 그날까지 데이터만으로"""
    bench = m.bench_close(code)
    c = df["Close"]
    n = len(c)
    out = {}
    for t in range(max(260, n - days), n):
        sub = df.iloc[: t + 1]
        try:
            r = m.analyze_df(sub, code, code, light=True)
            r["rs60"] = m.rel_strength(sub["Close"], bench)
            ks = m.display_keys(m.review(r))
            out[c.index[t]] = (tuple(ks), round(r["rsi"], 1), round(r["disp"]["cur"], 1) if r.get("disp") else None)
        except Exception as e:
            out[c.index[t]] = (("오류:" + type(e).__name__,), None, None)
    return out


ACT = {"sell", "buyb", "buy", "up"}


def starts(keys_by_day):
    """매수·매도 신호가 새로 켜진 날 {(날짜, 매수|매도)}"""
    res, prev = set(), set()
    for d in sorted(keys_by_day):
        cur = {("매도" if k == "sell" else "매수") for k in keys_by_day[d][0] if k in ACT}
        for s in cur - prev:
            res.add((d, s))
        prev = cur
    return res


def sig_compare(ka, kb):
    days = sorted(set(ka) & set(kb))
    diff = [d for d in days if ka[d][0] != kb[d][0]]
    sa, sb = starts({d: ka[d] for d in days}), starts({d: kb[d] for d in days})
    rd = [abs(ka[d][1] - kb[d][1]) for d in days if ka[d][1] is not None and kb[d][1] is not None]
    dd = [abs(ka[d][2] - kb[d][2]) for d in days if ka[d][2] is not None and kb[d][2] is not None]
    return {"days": len(days), "diff_days": len(diff),
            "diff_ex": [[str(d.date()), "/".join(ka[d][0]) or "-", "/".join(kb[d][0]) or "-"] for d in diff[-8:]],
            "start_a": len(sa), "start_b": len(sb), "start_same": len(sa & sb),
            "start_only_a": sorted([f"{d.date()} {s}" for d, s in sa - sb])[-8:],
            "start_only_b": sorted([f"{d.date()} {s}" for d, s in sb - sa])[-8:],
            "rsi_max": max(rd) if rd else None, "disp_max": max(dd) if dd else None}


def run_one(arg):
    code, name = arg
    kr = m.is_kr(code)
    out = {"code": code, "name": name, "market": "kr" if kr else "us", "sources": {}}
    a, err = fetch(src_report, code)
    if a is None:
        out["error"] = "기준(리포트) 시세 실패: " + (err or "")
        return out
    out["n"] = len(a)
    out["first"] = str(a.index[0].date())
    out["last"] = str(a.index[-1].date())
    # 출처마다 마지막 봉이 다를 수 있음(장중 미완성 등) → 공통 마지막 날까지만 신호 비교
    ka = None
    for key, label, fn in (SOURCES_KR if kr else SOURCES_US):
        b, err = fetch(fn, code)
        s = {"label": label}
        if b is None:
            s["error"] = err
            out["sources"][key] = s
            continue
        s.update(compare(a, b))
        try:
            end = min(a.index[-1], b.index[-1])
            if ka is None or ka[0] != end:
                ka = (end, daily_keys(a[a.index <= end], code, SIG_DAYS))
            kb = daily_keys(b[b.index <= end], code, SIG_DAYS)
            s["sig"] = sig_compare(ka[1], kb)
        except Exception as e:
            s["sig_error"] = f"{type(e).__name__}: {e}"[:160]
        out["sources"][key] = s
    return out


def sample():
    mine = [(x["code"], x["name"]) for x in m.load_tickers() if x.get("code")]
    kr = [(c, n) for c, n in mine if m.is_kr(c)]
    us = [(c, n) for c, n in mine if not m.is_kr(c)]
    have = {kr_base(c) for c, _ in kr} | {c for c, _ in us}
    kr += [(c, n) for c, n in KR_EXTRA if c not in have]
    us += [(c, n) for c, n in US_EXTRA if c not in have]
    return kr[:22], us[:22]


# ---------------------------------------------------------------- 화면
def verdict(s):
    """한 칸 판정: 일치 / 조정 차이 / 불일치 / 실패"""
    if "error" in s:
        return "fail", "실패"
    if s.get("common", 0) < 30:
        return "fail", "공통 날짜 부족"
    if s.get("c_bad_n", 1) == 0:
        return "ok", "일치"
    if s.get("r_bad_n", 99) <= max(2, s["common"] * 0.01) and s.get("c_med", 0) > TOL_CLOSE / 5:
        return "adj", "조정 차이"          # 날마다의 등락은 같고 가격 수준만 다름(배당·분할 반영 방식)
    if s.get("c_med", 99) <= TOL_CLOSE:
        return "warn", "일부 날짜 차이"
    return "bad", "불일치"


def f(x, nd=2, suf=""):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}{suf}"


def write(results, secs):
    os.makedirs(OUT_DIR, exist_ok=True)
    now = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    meta = {"kind": "data_check", "version": VERSION, "memo": MEMO, "run": now, "secs": round(secs),
            "tol_close": TOL_CLOSE, "tol_vol": TOL_VOL, "sig_days": SIG_DAYS, "items": results}
    base = f"data_check_{VERSION}" if VERSION else "data_check"
    with open(os.path.join(OUT_DIR, base + ".json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=1, default=str)

    # 출처별 요약
    summ = {}
    for it in results:
        for key, s in it.get("sources", {}).items():
            g = summ.setdefault((it["market"], key), {"label": s["label"], "n": 0, "ok": 0, "adj": 0, "warn": 0, "bad": 0, "fail": 0,
                                                      "sig_n": 0, "sig_diff": 0, "st_a": 0, "st_same": 0})
            g["n"] += 1
            g[verdict(s)[0]] += 1
            sg = s.get("sig")
            if sg:
                g["sig_n"] += sg["days"]
                g["sig_diff"] += sg["diff_days"]
                g["st_a"] += sg["start_a"]
                g["st_same"] += sg["start_same"]
    css = """body{font:13px/1.5 -apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;margin:16px;color:#222;background:#fff}
h1{font-size:18px}h2{font-size:15px;margin-top:28px}table{border-collapse:collapse;margin:8px 0;font-size:12px}
td,th{border:1px solid #ddd;padding:3px 6px;text-align:right;white-space:nowrap}th{background:#f4f4f4}td.l,th.l{text-align:left}
.ok{background:#e6f6ea}.adj{background:#fff4d6}.warn{background:#fde9d9}.bad{background:#f9d0d0}.fail{background:#eee;color:#888}
.note{color:#666;font-size:12px;max-width:900px}details{margin:4px 0}summary{cursor:pointer}
@media (prefers-color-scheme:dark){body{background:#16171a;color:#ddd}th{background:#24262b}td,th{border-color:#333}
.ok{background:#1d3a26}.adj{background:#3d3418}.warn{background:#40291c}.bad{background:#4a1e1e}.fail{background:#2a2a2a;color:#888}.note{color:#999}}"""
    h = [f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>시세 출처 검증</title><style>{css}</style>",
         f"<h1>시세 출처 교차 검증</h1><p class='note'>실행 {now} KST · {secs/60:.0f}분 · 기준 = 리포트·백테스트 출처(국내 FinanceDataReader, 해외 FDR→yfinance 수정주가) · "
         f"종가 비교 최근 {CMP_DAYS}거래일 · 신호 비교 최근 {SIG_DAYS}거래일{(' · 메모: ' + MEMO) if MEMO else ''}</p>",
         "<p class='note'>판정: <span class='ok'>일치</span> = 모든 날 종가 차이 0.5% 이내 · "
         "<span class='adj'>조정 차이</span> = 날마다의 등락률은 같고 가격 수준만 다름(배당·분할 반영 방식 차이 — 과거로 갈수록 벌어짐, '비율' = 5년 앞뒤 비율 변화) · "
         "<span class='warn'>일부 날짜 차이</span> = 보통은 같지만 몇몇 날이 다름 · <span class='bad'>불일치</span> = 보통 날도 0.5% 넘게 다름. "
         "신호 차이 = 같은 날 V1 칸(매도·매수·보유 등)이 기준과 다르게 나온 날 비율, 신호 시작 일치 = 기준에서 매수·매도 신호가 새로 켜진 날 중 같은 날 켜진 비율.</p>",
         "<h2>출처별 요약</h2><table><tr><th class='l'>시장</th><th class='l'>비교 출처</th><th>종목</th><th>일치</th><th>조정 차이</th><th>일부 차이</th><th>불일치</th><th>실패</th><th>신호 다른 날</th><th>신호 시작 일치</th></tr>"]
    for (mk, key), g in sorted(summ.items()):
        sd = f"{g['sig_diff'] / g['sig_n'] * 100:.1f}% ({g['sig_diff']}/{g['sig_n']})" if g["sig_n"] else "-"
        st = f"{g['st_same'] / g['st_a'] * 100:.0f}% ({g['st_same']}/{g['st_a']})" if g["st_a"] else "-"
        h.append(f"<tr><td class='l'>{'국내' if mk == 'kr' else '해외'}</td><td class='l'>{g['label']}</td><td>{g['n']}</td>"
                 f"<td>{g['ok']}</td><td>{g['adj']}</td><td>{g['warn']}</td><td>{g['bad']}</td><td>{g['fail']}</td><td>{sd}</td><td>{st}</td></tr>")
    h.append("</table>")

    for mk, title in (("kr", "국내"), ("us", "해외")):
        items = [it for it in results if it["market"] == mk]
        if not items:
            continue
        keys = [k for k, _, _ in (SOURCES_KR if mk == "kr" else SOURCES_US)]
        labels = dict((k, l) for k, l, _ in (SOURCES_KR if mk == "kr" else SOURCES_US))
        h.append(f"<h2>{title} 종목별</h2><table><tr><th class='l'>종목</th><th>기준 봉 수</th>" +
                 "".join(f"<th>{labels[k]}<br><small>종가 중앙/99%/최대 · 5년 비율 · 거래량 중앙 · 신호 다른 날 · 시작 일치</small></th>" for k in keys) + "</tr>")
        for it in items:
            row = f"<tr><td class='l'>{it['name']}<br><small>{it['code']}</small></td><td>{it.get('n', '-')}<br><small>{it.get('first', '')}~</small></td>"
            if "error" in it:
                row += f"<td class='fail l' colspan='{len(keys)}'>{it['error']}</td></tr>"
                h.append(row)
                continue
            for k in keys:
                s = it["sources"].get(k, {"error": "없음"})
                cls, vt = verdict(s)
                if "error" in s:
                    row += f"<td class='fail l'>실패<br><small>{s['error'][:70]}</small></td>"
                    continue
                sg = s.get("sig") or {}
                sig = (f"{sg['diff_days']}/{sg['days']}일 · {sg['start_same']}/{sg['start_a']}" if sg else s.get("sig_error", "-")[:40])
                row += (f"<td class='{cls}'><b>{vt}</b> {f(s.get('c_med'))}/{f(s.get('c_p99'))}/{f(s.get('c_max'))}%<br>"
                        f"<small>비율 {f(s.get('drift'), 1, '%')} · 거래량 {f(s.get('v_med'), 1, '%')} · 신호 {sig}</small></td>")
            h.append(row + "</tr>")
        h.append("</table>")
        # 세부(문제 있는 것만)
        h.append(f"<h2>{title} 세부 (일치가 아닌 것)</h2>")
        for it in items:
            for k in keys:
                s = it.get("sources", {}).get(k)
                if not s or "error" in s or verdict(s)[0] == "ok" and not (s.get("sig") or {}).get("diff_days"):
                    continue
                sg = s.get("sig") or {}
                worst = "".join(f"<tr><td>{w[0]}</td><td>{w[1]}</td><td>{w[2]}</td><td>{w[3]:+.2f}%</td></tr>" for w in s.get("c_worst", []))
                ex = "".join(f"<tr><td>{e[0]}</td><td class='l'>{e[1]}</td><td class='l'>{e[2]}</td></tr>" for e in sg.get("diff_ex", []))
                h.append(f"<details><summary>{it['name']} · {labels[k]} — {verdict(s)[1]}, 신호 다른 날 {sg.get('diff_days', '-')}일</summary>"
                         f"<p class='note'>공통 {s.get('common')}일 · 기준에만 {s.get('only_a_n')}일 {', '.join(s.get('only_a', [])[-5:])} · "
                         f"비교에만 {s.get('only_b_n')}일 {', '.join(s.get('only_b', [])[-5:])} · 12년 최대 차이 {f(s.get('c_max_all'))}% ({s.get('c_max_all_date', '')}) · "
                         f"시작일 기준 {s.get('first_a', '')} / 비교 {s.get('first_b', '')} · 거래량 0인 날 기준 {s.get('v_zero_a', '-')} / 비교 {s.get('v_zero_b', '-')} · "
                         f"거래량 비율 중앙값 {f(s.get('v_ratio'), 3)} · RSI 최대 차이 {f(sg.get('rsi_max'), 1)} · 이격도 최대 차이 {f(sg.get('disp_max'), 1)}</p>"
                         f"<table><tr><th>종가 차이 큰 날</th><th>기준</th><th>비교</th><th>차이</th></tr>{worst}</table>"
                         + (f"<table><tr><th>신호 다른 날(최근)</th><th class='l'>기준</th><th class='l'>비교</th></tr>{ex}</table>" if ex else "")
                         + (f"<p class='note'>기준에만 켜진 신호: {', '.join(sg.get('start_only_a', []))}<br>비교에만 켜진 신호: {', '.join(sg.get('start_only_b', []))}</p>"
                            if sg.get("start_only_a") or sg.get("start_only_b") else "") + "</details>")
    html = "\n".join(h)
    for nm in ([base] + (["data_check"] if VERSION else [])):
        with open(os.path.join(OUT_DIR, nm + ".html"), "w", encoding="utf-8") as fp:
            fp.write(html)
    if VERSION:
        with open(os.path.join(OUT_DIR, "data_check.json"), "w", encoding="utf-8") as fp:
            json.dump(meta, fp, ensure_ascii=False, indent=1, default=str)


def main():
    t0 = time.time()
    kr, us = sample()
    todo = kr + us
    print(f"국내 {len(kr)} · 해외 {len(us)} 종목, 작업자 {WORKERS}")
    # 지수(상대강도용)는 미리 받아 두고 작업자에 물려줌(fork)
    m.bench_close("005930")
    m.bench_close("AAPL")
    if WORKERS > 1 and sys.platform != "win32":
        with mp.get_context("fork").Pool(WORKERS) as pool:
            results = pool.map(run_one, todo, chunksize=1)
    else:
        results = [run_one(x) for x in todo]
    for it in results:
        bad = [k for k, s in it.get("sources", {}).items() if verdict(s)[0] != "ok"]
        print(it["code"], it["name"], it.get("error", ""), "문제:", bad)
    write(results, time.time() - t0)
    print(f"끝 {time.time() - t0:.0f}초")


if __name__ == "__main__":
    main()
