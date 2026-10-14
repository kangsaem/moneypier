"""코스피 200일선 붕괴 국면 — 안정 기준(200일선 위 연속 일수) 40 / 50 / 60일 비교 (2026-10-14 사용자 확인 요청)

국면 = 안정된 상태에서 처음 200일선을 깬 날(전날 종가 > 전날 200일선, 그날 종가 < 그날 200일선)부터
       안정(상향 돌파일 포함 H거래일 연속 200일선 위)까지. 안정 전 재이탈은 같은 국면.
출력: results/k200_hold.txt — 기준별 국면 수·구간별 수·국면 목록, 2026-07-29 국면과 2026-10-13 이탈의 관계
"""
import os
import pandas as pd
import FinanceDataReader as fdr

BANDS = [(-5, "-5% 이상"), (-3, "-3~-5%"), (-2, "-2~-3%"), (-1, "-1~-2%"), (0, "0~-1%")]


def band(chg):
    for lim, name in BANDS:
        if chg <= lim:
            return name
    return "0~-1%"


def episodes(D, C, M, H):
    out, ep, n = [], None, len(C)
    i = 1
    while i < n:
        if M[i] != M[i] or M[i - 1] != M[i - 1]:           # NaN
            i += 1
            continue
        if ep is None:
            if C[i - 1] > M[i - 1] and C[i] < M[i]:
                ep = {"b": i, "date": D[i], "chg": (C[i] / C[i - 1] - 1) * 100, "rebreaks": [], "fails": 0}
            i += 1
            continue
        # 국면 안: 상향 돌파면 H일 버티는지 확인
        if C[i] > M[i] and C[i - 1] <= M[i - 1]:
            if i + H > n:                                   # 아직 판단 불가(진행 중)
                ep["open"] = True
                ep["upFrom"] = D[i]
                ep["upDays"] = n - i
                break
            if all(C[k] > M[k] for k in range(i, i + H)):
                ep["stab"] = D[i]
                ep["stabDays"] = i - ep["b"]
                out.append(ep)
                ep = None
                i = i + H                                   # 안정 확정까지 건너뜀
                continue
            ep["fails"] += 1
        elif C[i - 1] > M[i - 1] and C[i] < M[i]:
            ep["rebreaks"].append((D[i], (C[i] / C[i - 1] - 1) * 100))
        i += 1
    if ep is not None:
        ep.setdefault("open", True)
        out.append(ep)
    return out


def main():
    df = fdr.DataReader("KS11", "1995-01-01")
    s = df["Close"].dropna()
    s = s[s > 0]
    s.index = pd.to_datetime(s.index).strftime("%Y-%m-%d")
    s = s[~s.index.duplicated(keep="last")]
    D, C = list(s.index), [float(x) for x in s.values]
    M = list(pd.Series(C).rolling(200).mean())
    lines = [f"데이터 {D[0]} ~ {D[-1]} ({len(D)}거래일)", ""]
    base = None
    for H in (40, 50, 60):
        eps = episodes(D, C, M, H)
        dates = [e["date"] for e in eps]
        cnt = {name: 0 for _, name in BANDS}
        for e in eps:
            cnt[band(e["chg"])] += 1
        big = sum(1 for e in eps if e["chg"] <= -2)
        lines.append(f"=== 안정 기준 {H}거래일: 국면 {len(eps)}개 (첫 이탈 -2% 이상 {big}개) · " + " / ".join(f"{k} {v}" for k, v in cnt.items()))
        if base is None:
            base = set(dates)
        else:
            gone = sorted(base - set(dates))
            new = sorted(set(dates) - base)
            lines.append(f"  40일 기준 대비 빠진 국면(앞 국면에 합쳐짐): {', '.join(gone) or '없음'}")
            lines.append(f"  40일 기준 대비 새로 생긴 국면: {', '.join(new) or '없음'}")
        for e in eps:
            st = f"안정 {e['stab']} ({e['stabDays']}거래일 후)" if "stab" in e else ("진행 중" + (f" · {e['upFrom']}부터 200일선 위 {e['upDays']}일째" if "upFrom" in e else ""))
            rb = " · 재이탈 " + ", ".join(f"{d}({c:+.2f}%)" for d, c in e["rebreaks"]) if e["rebreaks"] else ""
            lines.append(f"  {e['date']} {e['chg']:+.2f}% [{band(e['chg'])}] {st} · 실패 {e['fails']}회{rb}")
        lines.append("")
    # 2026-07-29 국면 이후 200일선 위 연속 일수(07-31 돌파 ~ 10-13 이탈 전)
    if "2026-07-31" in D and "2026-10-13" in D:
        a, b = D.index("2026-07-31"), D.index("2026-10-13")
        run = 0
        for k in range(a, b):
            if C[k] > M[k]:
                run += 1
            else:
                break
        lines.append(f"2026-07-31 돌파 뒤 200일선 위 연속: {run}거래일 (2026-10-13 이탈 직전까지 {b - a}거래일, 2026-10-13 종가 {C[b]:.2f} vs 200일선 {M[b]:.2f})")
    os.makedirs("results", exist_ok=True)
    open("results/k200_hold.txt", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
