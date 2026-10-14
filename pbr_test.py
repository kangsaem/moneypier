"""코스피 PBR 저점 매수 시험 (2026-10-14 사용자 요청)

자료: KRX 코스피 지수 PBR(일별, pykrx get_index_fundamental, 코드 1001). 가격 = 같은 자료의 코스피 종가.
비교: 아무 날 매수 vs 'PBR 저점' 조건인 날 매수 → 6개월·1년·3년 뒤 수익(배당 제외 가격지수).
조건(미래를 보지 않음 — 그날까지 자료로만 판단):
  - 절대값: PBR ≤ 1.0 / 0.9 / 0.8
  - 상대값: 지난 10년(2,500거래일) PBR 중 하위 10% / 20% 안
  - '처음 들어간 날'만(조건이 꺼졌다 다시 켜진 첫날) vs '조건인 모든 날'
결과: results/pbr_test.txt, results/pbr_kospi.json(날짜·종가·PBR — 이후 화면용)
"""
import json, os, statistics as st, sys, time
import pandas as pd

def load():
    from pykrx import stock
    frames = []
    for y in range(2000, pd.Timestamp.today().year + 1):
        s, e = f"{y}0101", f"{y}1231"
        for t in range(3):
            try:
                df = stock.get_index_fundamental(s, e, "1001")
                break
            except Exception as ex:
                print("재시도", y, ex, file=sys.stderr); time.sleep(3); df = None
        if df is not None and len(df):
            frames.append(df)
        time.sleep(0.5)
    df = pd.concat(frames)
    df.index = pd.to_datetime(df.index)
    df = df[~df.index.duplicated()].sort_index()
    print("열:", list(df.columns), "행:", len(df), df.index[0].date(), "~", df.index[-1].date())
    df = df[(df["PBR"] > 0) & (df["종가"] > 0)]
    return df

def fwd(C, i, h):
    return (C[i + h] / C[i] - 1) * 100 if i + h < len(C) else None

def summ(v):
    v = [x for x in v if x is not None]
    if not v:
        return "-"
    return f"{len(v)}건 이익 {sum(x > 0 for x in v) / len(v) * 100:.0f}% 중간 {st.median(v):+.1f}% 평균 {st.mean(v):+.1f}% 최악 {min(v):+.1f}%"

def main():
    df = load()
    D = [d.strftime("%Y-%m-%d") for d in df.index]
    C = list(df["종가"].astype(float))
    P = list(df["PBR"].astype(float))
    n = len(C)
    os.makedirs("results", exist_ok=True)
    json.dump({"d": D, "c": C, "pbr": P}, open("results/pbr_kospi.json", "w"), separators=(",", ":"))
    H = [(126, "6개월"), (252, "1년"), (756, "3년")]
    out = [f"코스피 PBR 자료 {D[0]} ~ {D[-1]} ({n}거래일) · PBR 최저 {min(P):.2f} 최고 {max(P):.2f} 중간 {st.median(P):.2f}", ""]
    out.append("[아무 날] " + " | ".join(f"{hn}: {summ([fwd(C, i, h) for i in range(n)])}" for h, hn in H))
    # 조건들
    def pct_rank(i, win=2500):
        a = P[max(0, i - win):i + 1]
        return sum(x <= P[i] for x in a) / len(a)
    rank = [pct_rank(i) if i >= 750 else None for i in range(n)]       # 최소 3년 자료가 쌓인 뒤부터
    conds = {
        "PBR ≤ 1.0": [p <= 1.0 for p in P],
        "PBR ≤ 0.9": [p <= 0.9 for p in P],
        "PBR ≤ 0.8": [p <= 0.8 for p in P],
        "지난 10년 하위 20%": [r is not None and r <= 0.2 for r in rank],
        "지난 10년 하위 10%": [r is not None and r <= 0.1 for r in rank],
    }
    for nm, ok in conds.items():
        days = [i for i in range(n) if ok[i]]
        firsts = [i for i in days if i == 0 or not ok[i - 1]]
        # 20거래일 안에 다시 켜진 건 같은 구간으로(잦은 깜빡임 정리)
        f2 = []
        for i in firsts:
            if not f2 or i - f2[-1] > 60:
                f2.append(i)
        out.append("")
        out.append(f"[{nm}] 조건인 날 {len(days)}일({len(days) / n * 100:.0f}%) · 처음 들어간 날(60거래일 간격) {len(f2)}번: " + ", ".join(D[i] for i in f2))
        out.append("   조건인 모든 날 매수: " + " | ".join(f"{hn}: {summ([fwd(C, i, h) for i in days])}" for h, hn in H))
        out.append("   처음 들어간 날 매수: " + " | ".join(f"{hn}: {summ([fwd(C, i, h) for i in f2])}" for h, hn in H))
        # 처음 들어간 날 이후 1년 안 최대 하락
        dd = [(min(C[i:min(n, i + 253)]) / C[i] - 1) * 100 for i in f2]
        if dd:
            out.append(f"   처음 들어간 날 이후 1년 안 최대 하락: 중간 {st.median(dd):+.1f}% 최악 {min(dd):+.1f}%")
    # 같은 돈 비교: 매달 1 넣기 vs 평소 모아 두다 'PBR ≤ 0.9' 조건인 날 전부 넣기(현금 이자 0)
    out.append("")
    out.append("[같은 돈 비교] 매달 첫 거래일 1씩 입금. A = 바로 매수, B = 현금으로 모아 두다 조건인 날 모은 돈 전부 매수(조건 안 오면 현금 그대로)")
    months = [i for i in range(1, n) if D[i][:7] != D[i - 1][:7]]
    for nm in ("PBR ≤ 1.0", "PBR ≤ 0.9", "지난 10년 하위 20%"):
        ok = conds[nm]
        a_units = 0.0; b_units = 0.0; b_cash = 0.0; paid = 0
        mset = set(months)
        for i in range(n):
            if i in mset:
                paid += 1; a_units += 1 / C[i]; b_cash += 1
            if ok[i] and b_cash > 0:
                b_units += b_cash / C[i]; b_cash = 0
        a = a_units * C[-1]; b = b_units * C[-1] + b_cash
        out.append(f"   {nm}: 넣은 돈 {paid} → A 매달 매수 {a:.1f} ({(a / paid - 1) * 100:+.1f}%) · B 저점에만 매수 {b:.1f} ({(b / paid - 1) * 100:+.1f}%, 남은 현금 {b_cash:.0f})")
    txt = "\n".join(out)
    open("results/pbr_test.txt", "w").write(txt + "\n")
    print(txt)

if __name__ == "__main__":
    main()
