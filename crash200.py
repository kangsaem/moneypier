"""200일선 붕괴 사례 — 코스피·S&P500·나스닥 공통 계산 (2026-10-14)

머니파이 kospi200.html · spx200.html · ndx200.html 이 읽는 results/crash200_<key>.json 을 만든다.
국면 = 안정된 상태에서 처음 200일선을 깬 날(전날 종가 > 전날 200일선, 그날 종가 < 그날 200일선) ~ 200일선 안정.
  안정 = 상향 돌파일 포함 40거래일 연속 200일선 위(그 돌파일). 그 전 재이탈은 같은 국면, 실패 = 40일 못 버틴 상향 돌파 수.
  이후 바닥까지 = 이탈일 종가 → 안정 전까지 최저 종가.
  60주선 = 주봉 종가 60주 평균(이번 주는 그날 종가). 도달 = 이탈일부터 안정 전까지 일봉 저가 ≤ 그날 60주선인 첫날, 이탈일 종가가 이미 아래면 '이미 아래'.
  지수회복 = 안정일(포함) 이후 처음 종가 ≥ 이탈 전날 종가인 날, 일수는 이탈일 = 1일.
그래프 창: 이탈 전날 ~ max(지수회복일, 안정일 + 39거래일)(아직이면 마지막 날).
  핵심 구간(이탈 전날 ~ max(지수회복일, 안정일+39))이 63거래일 미만이면 63거래일 창으로, 핵심 구간을 가운데 두고 좌우를 같은 길이로 채움(화면에서 어둡게 덮음).
  이평 계산용으로 창 앞 PRE 거래일을 같이 넣음(60주선 ≈ 300거래일).
"""
import json, os, sys
import pandas as pd

H, PRE, SHORT = 40, 330, 63
CASE_FROM = "1996-01-01"
COVID = ("2020-02-20", "2020-03-31")                      # 코로나19 급락 구간(국면이 겹치면 이슈에 '+ 코로나19')
IDX = {  # key: (이름, FDR 코드, 야후 심볼, 데이터 시작)
    "kospi": ("코스피", "KS11", "^KS11", "1995-01-01"),
    "spx": ("S&P500", "US500", "^GSPC", "1990-01-01"),
    "ndx": ("나스닥", "IXIC", "^IXIC", "1990-01-01"),
}


def load(key):
    nm, fcode, ysym, start = IDX[key]
    df = None
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(fcode, start)
        if df is None or len(df) < 2000 or "Close" not in df:
            raise ValueError("FDR 부족")
        if "Low" not in df:
            df["Low"] = df["Close"]
    except Exception as e:
        print(key, "FDR 실패 → 야후", e, file=sys.stderr)
        import yfinance as yf
        df = yf.download(ysym, start=start, auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
    df = df[["Close", "Low"]].copy()
    df = df[df["Close"] > 0]
    df["Low"] = df["Low"].where(df["Low"] > 0, df["Close"])
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df = df[~df.index.duplicated(keep="last")]
    return list(df.index), [float(x) for x in df["Close"]], [float(x) for x in df["Low"]]


def week60(D, C):
    """i번째 날의 60주선(지난 59주 마지막 종가 + 그날 종가) / 자료 부족이면 None"""
    wk = [pd.Timestamp(d).isocalendar()[:2] for d in D]
    out, last = [None] * len(D), []                         # last = 끝난 주들의 마지막 종가
    for i in range(len(D)):
        if i > 0 and wk[i] != wk[i - 1]:
            last.append(C[i - 1])
        if len(last) >= 59:
            out[i] = (sum(last[-59:]) + C[i]) / 60
    return out


def episodes(D, C, M):
    eps, ep, n, i = [], None, len(C), 1
    while i < n:
        if M[i] is None or M[i - 1] is None:
            i += 1
            continue
        if ep is None:
            if C[i - 1] > M[i - 1] and C[i] < M[i]:
                ep = {"b": i, "fails": 0, "u": None}
            i += 1
            continue
        if C[i] > M[i] and C[i - 1] <= M[i - 1]:
            if i + H > n:                                    # 아직 판단 불가 → 진행 중
                break
            if all(C[k] > M[k] for k in range(i, i + H)):
                ep["u"] = i
                eps.append(ep)
                ep = None
                i += H
                continue
            ep["fails"] += 1
        i += 1
    if ep is not None:
        eps.append(ep)
    return eps


def build(key):
    D, C, L = load(key)
    n = len(C)
    M = [None] * n
    s = 0.0
    for i in range(n):
        s += C[i]
        if i >= 200:
            s -= C[i - 200]
        if i >= 199:
            M[i] = s / 200
    W = week60(D, C)
    out = []
    eps = episodes(D, C, M)
    for k, ep in enumerate(eps):
        b, u = ep["b"], ep["u"]
        if D[b] < CASE_FROM:
            continue
        p = b - 1
        pc = C[p]
        end_d = u if u is not None else n                     # 바닥·60주선은 안정 전까지
        mi = min(range(b, end_d), key=lambda j: C[j])
        drop = (C[mi] / C[b] - 1) * 100
        w = W[b]
        if w is None:
            w60 = {"na": True}
        elif C[b] <= w:
            w60 = {"already": True}
        else:
            hit = next((j for j in range(b, end_d) if W[j] is not None and L[j] <= W[j]), None)
            w60 = ({"days": hit - b, "date": D[hit], "pc": round((W[hit] / pc - 1) * 100, 2)}   # pc = 닿은 날 60주선 vs 이탈 전날 종가(2026-10-15)
                   if hit is not None else {"none": True})
        w60b = (C[b] / w - 1) * 100 if w else None
        r = next((j for j in range(u, n) if C[j] >= pc), None) if u is not None else None
        rec_n = (r - b + 1) if r is not None else None
        # 그래프 창
        if u is None:
            R = n - 1
        else:
            R = min(n - 1, max(r if r is not None else n - 1, u + H - 1))
        core_end = R
        Lw, gray = p, []
        core = R - p + 1                                         # 핵심 구간 = 이탈 전날 ~ max(지수회복일, 안정일+39)
        if u is not None and core < SHORT:                      # 63거래일(3달) 미만이면 63거래일 창, 핵심 구간을 가운데 두고 좌우 같은 길이로 채움(어둡게 덮음)
            padl = (SHORT - core) // 2
            padr = SHORT - core - padl
            Lw, R2 = max(0, p - padl), min(n - 1, R + padr)
            if Lw < p:
                gray.append([Lw, p - 1])
            if R2 > R:
                gray.append([R + 1, R2])
            R = R2
        S = max(0, Lw - PRE)
        nxt = eps[k + 1]["b"] if k + 1 < len(eps) else None
        span_end = core_end                                     # 코로나 겹침은 핵심 구간 기준
        covid = D[b] <= COVID[1] and D[span_end] >= COVID[0]
        g = {"d": [x.replace("-", "") for x in D[S:R + 1]], "c": [round(x, 2) for x in C[S:R + 1]],
             "from": Lw - S, "brk": b - S, "stab": (u - S) if u is not None else None, "rec": (r - S) if r is not None else None,
             "gray": [[a - S, b - S] for a, b in gray] or None}
        out.append({
            "date": D[b], "chg": round((C[b] / C[p] - 1) * 100, 2), "prevGap": round((C[p] / M[p] - 1) * 100, 2),
            "drop": round(drop, 2), "dropDays": mi - b,
            "w60": w60, "w60b": round(w60b, 2) if w60b is not None else None,
            "stab": D[u] if u is not None else None, "stabDays": (u - b) if u is not None else None,
            "stabPct": round((C[u] / pc - 1) * 100, 2) if u is not None else None,
            "fails": ep["fails"], "rec": D[r] if r is not None else None, "recN": rec_n,
            "next": D[nxt] if nxt is not None else None, "covid": covid, "open": u is None, "g": g,
        })
    os.makedirs("results", exist_ok=True)
    # 다른 지수 겹쳐 보기용 전체 일봉(2026-10-14): 날짜(yyyymmdd)·종가
    json.dump({"key": key, "made": D[-1], "d": [x.replace("-", "") for x in D], "c": [round(x, 2) for x in C]},
              open(f"results/crash200_series_{key}.json", "w"), separators=(",", ":"))
    res = {"key": key, "name": IDX[key][0], "made": D[-1], "from": D[0], "hold": H, "cases": out}
    os.makedirs("results", exist_ok=True)
    json.dump(res, open(f"results/crash200_{key}.json", "w"), ensure_ascii=False, separators=(",", ":"))
    lines = [f"[{key}] {IDX[key][0]} 데이터 {D[0]}~{D[-1]} · 국면 {len(out)}개"]
    for c in out:
        w = c["w60"]
        ws = "이미 아래" if w.get("already") else "자료 부족" if w.get("na") else "미도달" if w.get("none") else f"{w['days']}거래일"
        lines.append(f"  {c['date']} {c['chg']:+.2f}% 전날 {c['prevGap']:+.1f}% 바닥 {c['drop']:+.1f}%({c['dropDays']}) 60주 {ws} "
                     f"안정 {c['stab']}({c['stabDays']}) 실패 {c['fails']} 지수회복 {c['rec']}({c['recN']}) 다음 {c['next']}{' 코로나' if c['covid'] else ''}{' 진행중' if c['open'] else ''}")
    return "\n".join(lines)


if __name__ == "__main__":
    txt = "\n\n".join(build(k) for k in (sys.argv[1:] or list(IDX)))
    open("results/crash200.txt", "w").write(txt + "\n")
    print(txt)
