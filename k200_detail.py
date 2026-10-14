"""코스피 200일선 붕괴 사례 — 사례별 추가 값 + 그래프용 일봉 (2026-10-14)

머니파이 kospi200.html 의 22개 국면(첫 이탈일)마다:
  - 이탈 전날 종가, 200일선 안정일(= 회복: 200일선 위 40거래일 유지 첫 돌파일)·그날 종가·이탈 전날 대비 %
  - 지수회복일(= 이탈일 뒤 처음으로 종가가 이탈 전날 종가 이상인 날)
  - 그래프 일봉: 이탈 전날 260거래일 앞(이평 계산용)부터 끝 날(안정일·지수회복일 중 늦은 날, 아직이면 마지막 날)까지
결과: results/kospi200_detail.json (머니파이 저장소에 복사해 씀)
안정일 정의는 두 가지(돌파일 포함 40일 / 돌파일 다음 40일)를 모두 계산해 페이지 표와 맞는 쪽을 보고함.
"""
import json, os
import pandas as pd
import FinanceDataReader as fdr

CASES = ["2002-06-26", "2004-05-10", "2024-08-05", "2026-07-29", "2000-01-25", "2020-02-24", "2008-01-11", "2011-08-04",
         "2018-02-07", "1996-05-21", "1997-08-29", "2006-06-01", "2013-04-05", "2014-01-03", "2014-10-01", "2015-08-03",
         "2016-11-02", "2021-09-28", "2023-09-26", "2010-02-08", "2010-05-18", "2012-05-15"]
PAGE_REC = {"2002-06-26": "2003-06-04", "2004-05-10": "2004-10-28", "2024-08-05": "2025-05-07", "2026-07-29": "2026-07-31",
            "2000-01-25": "2001-11-05", "2020-02-24": "2020-06-16", "2008-01-11": "2009-04-09", "2011-08-04": "2012-01-31",
            "2018-02-07": "2019-12-10", "1996-05-21": "1997-05-19", "1997-08-29": "1998-10-29", "2006-06-01": "2006-10-13",
            "2013-04-05": "2013-09-05", "2014-01-03": "2014-06-26", "2014-10-01": "2015-03-13", "2015-08-03": "2016-07-11",
            "2016-11-02": "2016-12-08", "2021-09-28": "2023-03-22", "2023-09-26": "2024-02-01", "2010-02-08": "2010-02-09",
            "2010-05-18": "2010-06-10", "2012-05-15": "2012-11-29"}
HOLD, PRE = 40, 260


def load():
    df = fdr.DataReader("KS11", "1995-01-01")
    s = df["Close"].dropna()
    s = s[s > 0]
    s.index = pd.to_datetime(s.index).strftime("%Y-%m-%d")
    return s[~s.index.duplicated(keep="last")]


def stab_day(D, C, M, d, incl):
    """이탈일 d 뒤 첫 '안정' 돌파일 인덱스. incl=True: 돌파일 포함 40일 연속 위 / False: 돌파일 다음 40일 위"""
    n = len(C)
    for u in range(d + 1, n):
        if not (C[u] > M[u] and C[u - 1] <= M[u - 1]):
            continue
        a, b = (u, u + HOLD) if incl else (u + 1, u + 1 + HOLD)
        if b > n:
            return None                                     # 아직 판단할 날이 모자람
        if all(C[k] > M[k] for k in range(a, b)):
            return u
    return None


def main():
    s = load()
    D, C = list(s.index), [float(x) for x in s.values]
    M = list(pd.Series(C).rolling(200).mean())
    pos = {d: i for i, d in enumerate(D)}
    out, check = {}, []
    for dt in CASES:
        if dt not in pos:
            check.append(f"{dt}: 데이터에 없음")
            continue
        d = pos[dt]
        p = d - 1
        pc = C[p]
        ui = stab_day(D, C, M, d, True)
        ux = stab_day(D, C, M, d, False)
        check.append(f"{dt}: 페이지 {PAGE_REC[dt]} / 포함 {D[ui] if ui is not None else '-'} / 다음 {D[ux] if ux is not None else '-'}")
        u = ui if ui is not None and D[ui] == PAGE_REC[dt] else (ux if ux is not None and D[ux] == PAGE_REC[dt] else ui)
        r = next((k for k in range(d + 1, len(C)) if C[k] >= pc), None)
        end = max([x for x in (u, r) if x is not None], default=len(C) - 1)
        if u is None or r is None:
            end = len(C) - 1                                # 둘 중 하나라도 아직이면 마지막 날까지
        st = max(0, p - PRE)
        out[dt] = {
            "prev": D[p], "prevClose": round(pc, 2),
            "stab": D[u] if u is not None else None, "stabDays": (u - d) if u is not None else None,
            "stabClose": round(C[u], 2) if u is not None else None,
            "stabPct": round((C[u] / pc - 1) * 100, 2) if u is not None else None,
            "rec": D[r] if r is not None else None, "recDays": (r - d) if r is not None else None,
            "recClose": round(C[r], 2) if r is not None else None,
            "from": D[p], "to": D[end],
            "d": [x.replace("-", "") for x in D[st:end + 1]], "c": [round(x, 2) for x in C[st:end + 1]],
        }
    os.makedirs("results", exist_ok=True)
    json.dump({"made": D[-1], "cases": out}, open("results/kospi200_detail.json", "w"), ensure_ascii=False, separators=(",", ":"))
    print("\n".join(check))
    for dt, v in out.items():
        print(dt, "안정", v["stab"], v["stabDays"], v["stabPct"], "| 지수회복", v["rec"], v["recDays"], "| 그래프", v["from"], "~", v["to"], len(v["d"]))


if __name__ == "__main__":
    main()
