"""200일선 붕괴 통계(2026-10-16 사용자 질문) — 붕괴 사례 페이지(kospi200·spx200·ndx200)에 넣을 표 3개.
자료: results/crash200_{key}.json(국면별 사례) · results/crash200_series_{key}.json(일봉 종가). crash200 Actions를 다시 돌리면 갱신.
기간: 1996-01 이후(코스피 1996-01-05 = 200일선 첫 계산일, S&P500·나스닥 종합 1996-01-02).
  ① 이탈 전날 200일선 대비 위치(prevGap): 국면별 과거 사례(진행 중 제외)의 최소·최대·두 번째 최대·중간값
  ② 200일선 위 X%(1·2·3) 이내인 날 → 다음 날 / 5거래일 안에 종가가 200일선 아래로 간 적 있으면 '붕괴'(돌파 후 재붕괴 포함, 날짜 단위라 겹침 많음)
  ③ 한 시장 국면 이탈 뒤 다른 시장: 그 전 거래일에 이미 아래 / 이후 5·20·60거래일 안 처음 종가 이탈 / 60일 안 없음.
     미장은 같은 날짜라도 코스피 마감 뒤 → 코스피 이탈 당일 미장 이탈 = 이후 1일. 미장 → 코스피는 다음 날부터.
실행: python crash200_stats.py  (결과를 그대로 출력)"""
import bisect
import json
import statistics as st

KEYS = (("kospi", "코스피"), ("spx", "S&P500"), ("ndx", "나스닥 종합"))
START = "19960101"


def load(k):
    s = json.load(open(f"results/crash200_series_{k}.json", encoding="utf-8"))
    d, c = s["d"], s["c"]
    ma, run = [None] * len(c), 0.0
    for i, x in enumerate(c):
        run += x
        if i >= 200:
            run -= c[i - 200]
        if i >= 199:
            ma[i] = run / 200
    cases = json.load(open(f"results/crash200_{k}.json", encoding="utf-8"))["cases"]
    return d, c, ma, cases


DATA = {k: load(k) for k, _ in KEYS}

print("① 이탈 전날 종가의 200일선 대비 위치(%) — 과거 사례(진행 중 제외)")
for k, nm in KEYS:
    cases = [x for x in DATA[k][3] if not x.get("open")]
    v = sorted((x["prevGap"], x["date"]) for x in cases)
    print(f"  {nm}: {len(v)}건 · 최소 {v[0][0]:+.2f} ({v[0][1]}) · 최대 {v[-1][0]:+.2f} ({v[-1][1]}) · "
          f"두 번째 최대 {v[-2][0]:+.2f} ({v[-2][1]}) · 중간 {st.median(x for x, _ in v):+.2f}")

print("\n② 200일선 위 X% 이내인 날 → 붕괴(버팀 / 붕괴 / 붕괴 비율)")
for k, nm in KEYS:
    d, c, ma, _ = DATA[k]
    row = []
    for X in (1, 2, 3):
        for H in (1, 5):
            brk = hold = 0
            for i in range(len(c) - H):
                if ma[i] is None or d[i] < START:
                    continue
                g = (c[i] / ma[i] - 1) * 100
                if 0 < g <= X:
                    if any(c[i + h] < ma[i + h] for h in range(1, H + 1)):
                        brk += 1
                    else:
                        hold += 1
            row.append(f"{X}% {'다음 날' if H == 1 else '5일 안'} {hold}/{brk} ({brk / (brk + hold) * 100:.0f}%)")
    print(f"  {nm}: " + " · ".join(row))

print("\n③ 먼저 이탈(국면) → 다른 시장")
BELOW = {}
for k, _ in KEYS:
    d, c, ma, _ = DATA[k]
    BELOW[k] = ([d[i] for i in range(len(c)) if ma[i]], {d[i]: c[i] < ma[i] for i in range(len(c)) if ma[i]})


def follow(a, b, same_day):
    db, bb = BELOW[b]
    out = []
    for x in DATA[a][3]:
        D = x["date"].replace("-", "")
        i = bisect.bisect_left(db, D) if same_day else bisect.bisect_right(db, D)
        if i > 0 and bb[db[i - 1]]:
            out.append(("already", None))
            continue
        lag = next((j - i + 1 for j in range(i, min(i + 60, len(db))) if bb[db[j]]), None)
        out.append(("after", lag))
    lags = [g for t, g in out if t == "after" and g]
    w = lambda n: sum(1 for g in lags if g <= n)
    none = sum(1 for t, g in out if t == "after" and g is None)
    med = f" · 지연 중간 {st.median(lags):g}거래일" if lags else ""
    return (f"{len(out)}건 · 이미 아래 {sum(1 for t, _ in out if t == 'already')} · 5일 안 {w(5)} · 20일 안 {w(20)} · "
            f"60일 안 {w(60)} · 60일 안 없음 {none}{med}")


for a, b, same in (("kospi", "spx", True), ("kospi", "ndx", True), ("spx", "kospi", False), ("ndx", "kospi", False)):
    print(f"  {dict(KEYS)[a]} → {dict(KEYS)[b]}: {follow(a, b, same)}")

print("\n④ ③과 같지만 따라간 쪽도 국면 기준(그 지수 붕괴 사례의 이탈일 · 국면 = 이탈일 ~ 200일선 안정일)")


def tdays(k, a, b):
    d = DATA[k][0]
    return bisect.bisect_left(d, b) - bisect.bisect_left(d, a)


PH = {k: [(x["date"].replace("-", ""), (x.get("stab") or "99999999").replace("-", "")) for x in DATA[k][3]] for k, _ in KEYS}


def follow_phase(a, b, same):
    out = []
    for L, _ in PH[a]:
        on = [p for p in PH[b] if (p[0] < L if same else p[0] <= L) and p[1] > L]
        if on:
            out.append(("already", tdays(b, on[-1][0], L)))
            continue
        nxt = [p for p in PH[b] if (p[0] >= L if same else p[0] > L)]
        out.append(("after", tdays(b, L, nxt[0][0]) + (1 if same else 0) if nxt else None))
    al = [g for t, g in out if t == "already"]
    lg = [g for t, g in out if t == "after" and g is not None]
    w = lambda n: sum(1 for g in lg if g <= n)
    med = f" · 지연 중간 {st.median([g for g in lg if g <= 60]):g}거래일" if w(60) else ""
    return (f"{len(out)}건 · 이미 국면 중 {len(al)}(20거래일 이내 시작 {sum(1 for g in al if g <= 20)} · 그 전 {sum(1 for g in al if g > 20)}) · "
            f"5일 안 {w(5)} · 20일 안 {w(20)} · 60일 안 {w(60)} · 60일 안 없음 {len(out) - len(al) - w(60)}{med}")


for a, b, same in (("kospi", "spx", True), ("kospi", "ndx", True), ("spx", "kospi", False), ("ndx", "kospi", False)):
    print(f"  {dict(KEYS)[a]} → {dict(KEYS)[b]}: {follow_phase(a, b, same)}")
