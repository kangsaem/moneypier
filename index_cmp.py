"""지수 200일선 시험(2026-10-13, 리포트·기록과 무관한 별도 시험)

지수 하나(S&P500·나스닥100·코스피)에 규칙을 적용해 그냥 보유와 비교. 수익은 원래 시드 100% 기준(추가 자금 손익도 원래 시드 대비),
단기 자금 이자·배당·세금·환율 제외. 판정 = 그날 지수 종가 vs 200일선, 체결 = 다음 거래일 종가. 비용 매수 0.05% · 매도 0.25%.

규칙
  hold          그냥 보유
  cut50·cut30   200일선 아래로 깨지면 현금 50·30%까지 매도, 다시 위로 오면 전부 매수
  B1            깨지면 +100%(그때 내 평가액만큼) 추가 투입, 다시 위로 오면 회수
  B2            깨지면 +100% 투입, 252거래일 뒤 회수(투입가 대비 −20%면 먼저 회수)
  C1·C2·C3 × R1·R2·R3   다시 위로 올라올 때 +100% 투입
      C1 모든 회복 / C2 직전 고점(깨진 날 기준 252일 최고) 대비 −15% 이상 빠졌던 회복만 / C3 200일선 +3% 위 또는 10일 연속 위일 때
      R1 252거래일 뒤 한 번에 / R2 126·252·378거래일에 1/3씩 / R3 다시 깨질 때까지
      공통: 회수 전에 다시 200일선 아래로 깨지면 남은 추가분 전부 회수
  D × R1·R2·R3  cut50 + C2 투입
기초 통계: 200일선 아래로 갔다가 다시 올라온 경우마다 직전 하락 깊이·아래 있던 기간·다시 깨질 때까지 기간·63/126/252/504일 뒤 수익·252일 안 최대 하락
기간: 전체(지수별 자료 시작 ~ 오늘) + 5년 구간(매달 시작)
덤: 이미 돌린 sp1(S&P 과거)·bear4(코스피 1997~) 결과 기간의 지수 수익 → results/index_windows.json
결과: results/index_cmp.json · index_cmp.html
"""
import glob
import json
import os
from statistics import mean, median

import numpy as np
import pandas as pd

import market_report as m

BUY, SELL = 0.0005, 0.0025
m.START = "1988-01-01"
IDX = [("spx", "S&P500", "US500", "^GSPC"), ("ndx", "나스닥100", "NDX", "^NDX"), ("kospi", "코스피", "KS11", "^KS11")]
WIN = 1260


def load(fdr_code, yf_code):
    s = None
    try:
        s = m._index_close(fdr_code, yf_code)
    except Exception as e:
        print("지수 실패", fdr_code, e)
    if fdr_code == "NDX" and (s is None or len(s) < 5000 or s.index[0].year > 1990):   # FDR에 없으면 야후
        import yfinance as yf
        s = yf.Ticker(yf_code).history(start=m.START)["Close"].dropna()
        s.index = s.index.tz_localize(None)
    s = s[~s.index.duplicated()].sort_index()
    return s[s > 0]


RULES = ["hold", "cut50", "cut30", "B1", "B2"] + [f"{c}{r}" for c in ("C1", "C2", "C3", "D") for r in ("R1", "R2", "R3")]


def sim(P, below, peak_dd, i0, i1, rule):
    """P 가격 배열, below[i] = 200일선 아래, i0~i1 구간. 원래 시드 1 → 내 평가액 경로, 투입 기록"""
    cut = {"cut50": 0.5, "cut30": 0.3}.get(rule, 0.5 if rule[0] == "D" else 0.0)
    base_u = (1 - BUY) / P[i0]          # 내 돈으로 산 단위
    cash = 0.0
    x_u, x_loan, x_t, x_px, x_l0, x_g = 0.0, 0.0, None, None, 0.0, 0.0     # 추가분 단위·남은 빌린 원금·투입일·투입가·처음 원금·누적 손익
    reduced = False
    injects = []                        # (손익 비율)
    pend = []                           # 다음 날 할 일
    up_days, armed, ep_dd = 0, False, 0.0
    own = []

    def withdraw(i, frac):
        nonlocal x_u, x_loan, cash, x_g
        if x_u <= 0:
            return
        u = x_u * frac
        loan = x_loan * frac
        g = u * P[i] * (1 - SELL) - loan
        cash += g
        x_g += g
        x_u -= u
        x_loan -= loan
        if frac >= 0.999:
            x_u, x_loan = 0.0, 0.0
            injects.append(x_g / x_l0 if x_l0 else 0)

    for i in range(i0, i1 + 1):
        p = P[i]
        # 1) 어제 신호의 체결(오늘 종가)
        for act in pend:
            if act == "cut" and not reduced:
                E = base_u * p + cash
                need = cut * E - cash
                if need > 0:
                    f = min(1.0, need / (base_u * p * (1 - SELL)))
                    cash += base_u * f * p * (1 - SELL)
                    base_u *= 1 - f
                reduced = True
            elif act == "rebuy" and reduced:
                base_u += cash * (1 - BUY) / p
                cash, reduced = 0.0, False
            elif act == "inject" and x_u <= 0:
                E = base_u * p + cash
                if E > 0:
                    x_u = E * (1 - BUY) / p
                    x_loan, x_t, x_px, x_l0, x_g = E, i, p, E, 0.0
            elif act == "out" and x_u > 0:
                withdraw(i, 1.0)
        pend = []
        # 2) 단계 회수(시간 기준)는 오늘 종가에 바로
        if x_u > 0 and x_t is not None:
            d = i - x_t
            if rule == "B2" and (d >= 252 or p <= x_px * 0.8):
                pend.append("out")
            elif rule.endswith("R1") and d >= 252:
                pend.append("out")
            elif rule.endswith("R2") and d in (126, 252, 378):
                if d == 378:
                    pend.append("out")
                else:
                    withdraw(i, 1.0 / 3 if d == 126 else 0.5)      # 1/3씩(남은 것의 1/3 → 1/2 → 전부)
        # 3) 오늘 종가로 신호 → 내일 체결
        b, pb = below[i], below[i - 1]
        if b and not pb:                                     # 깨짐
            if rule in ("cut50", "cut30") or rule[0] == "D":
                pend.append("cut")
            if rule in ("B1", "B2"):
                pend.append("inject")
            if rule[0] in "CD" and x_u > 0:
                pend.append("out")
            armed, up_days = False, 0
        if not b and pb:                                     # 다시 위로
            if rule in ("cut50", "cut30") or rule[0] == "D":
                pend.append("rebuy")
            if rule == "B1" and x_u > 0:
                pend.append("out")
            dd_ok = peak_dd[i] <= -0.15
            if rule.startswith("C1"):
                pend.append("inject")
            elif rule.startswith("C2") or rule[0] == "D":
                if dd_ok:
                    pend.append("inject")
            elif rule.startswith("C3"):
                armed, up_days = True, 0
        if rule.startswith("C3") and armed and not b:
            up_days += 1
        if rule.startswith("C3") and armed and not b and x_u <= 0 and (up_days >= 10 or p >= MA[i] * 1.03):
            pend.append("inject")
            armed = False
        own.append(base_u * p + cash + (x_u * p - x_loan if x_u > 0 else 0.0))
    if x_u > 0:                                               # 끝날에 남은 추가분은 회수했다고 보고 손익 기록
        x_g += x_u * P[i1] * (1 - SELL) - x_loan
        injects.append(x_g / x_l0 if x_l0 else 0)
    v = np.array(own)
    mdd = float(((v / np.maximum.accumulate(v)) - 1).min() * 100)
    return {"roc": float((v[-1] - 1) * 100), "mdd": mdd, "n_inj": len(injects), "inj": injects, "path": v}


MA = None


def episodes(P, below, dates):
    """200일선 아래 → 다시 위 경우들"""
    out, n = [], len(P)
    i = 201
    while i < n:
        if below[i] and not below[i - 1]:
            s = i
            pk = P[max(0, s - 252):s + 1].max()
            j = s
            while j < n and below[j]:
                j += 1
            if j >= n:
                break
            low = P[s:j].min()
            k = j
            while k < n and not below[k]:
                k += 1
            r = {"down": str(dates[s].date()), "up": str(dates[j].date()), "below_days": j - s, "depth": float((low / pk - 1) * 100),
                 "above_days": (k - j) if k < n else None, "open": k >= n}
            for h in (63, 126, 252, 504):
                r[f"r{h}"] = float((P[j + h] / P[j] - 1) * 100) if j + h < n else None
            seg = P[j:min(n, j + 253)]
            r["mdd252"] = float(((seg / np.maximum.accumulate(seg)) - 1).min() * 100)
            out.append(r)
            i = j
        i += 1
    return out


def main():
    global MA
    res = {"idx": {}}
    series = {}
    for key, nm, fc, yc in IDX:
        s = load(fc, yc)
        series[key] = s
        P = s.to_numpy(dtype=float)
        dates = s.index
        MA = pd.Series(P).rolling(200).mean().to_numpy()
        below = np.nan_to_num(P < MA, nan=False).astype(bool)
        below[:200] = False
        # 깨진 에피소드의 직전 고점 대비 최저(회복 시점에 알 수 있는 값): 다시 위로 오는 날 기준
        peak_dd = np.zeros(len(P))
        s0 = None
        for i in range(201, len(P)):
            if below[i] and not below[i - 1]:
                s0 = i
            if not below[i] and below[i - 1] and s0 is not None:
                pk = P[max(0, s0 - 252):s0 + 1].max()
                peak_dd[i] = P[s0:i].min() / pk - 1
        print(nm, dates[0].date(), "~", dates[-1].date(), len(P))
        ep = episodes(P, below, dates)
        start = 260
        full = {r: sim(P, below, peak_dd, start, len(P) - 1, r) for r in RULES}
        wins = {r: [] for r in RULES}
        starts = []
        last_m = None
        for i in range(start, len(P) - WIN):
            mo = (dates[i].year, dates[i].month)
            if mo != last_m:
                starts.append(i)
                last_m = mo
        for i0 in starts:
            for r in RULES:
                x = sim(P, below, peak_dd, i0, i0 + WIN - 1, r)
                wins[r].append((x["roc"], x["mdd"], x["inj"]))
        agg = {}
        for r in RULES:
            rr = [w[0] for w in wins[r]]
            md = [w[1] for w in wins[r]]
            inj = [g for w in wins[r] for g in w[2]]
            srt = sorted(rr)
            agg[r] = {"mean": mean(rr), "med": median(rr), "p10": srt[int(0.1 * (len(srt) - 1))], "worst": srt[0], "loss": sum(x < 0 for x in rr),
                      "mdd": mean(md), "mdd_worst": min(md), "beat_hold": sum(a > b for a, b in zip(rr, [w[0] for w in wins["hold"]])),
                      "full": full[r]["roc"], "full_mdd": full[r]["mdd"], "full_inj": full[r]["n_inj"],
                      "full_inj_fail": sum(g < 0 for g in full[r]["inj"]), "full_inj_mean": mean(full[r]["inj"]) * 100 if full[r]["inj"] else None}
        res["idx"][key] = {"name": nm, "from": str(dates[0].date()), "to": str(dates[-1].date()), "n_win": len(starts),
                           "below_pct": float(below[200:].mean() * 100), "episodes": ep, "agg": agg}
        for r in ("hold", "cut50", "B1", "B2", "C1R2", "C2R2", "C3R2", "DR2"):
            a = agg[r]
            print(f"  {r:6s} 5년 평균 {a['mean']:+.0f} 중앙 {a['med']:+.0f} 하위10 {a['p10']:+.0f} 최악 {a['worst']:+.0f} MDD {a['mdd']:.0f} | 전체 {a['full']:+.0f} MDD {a['full_mdd']:.0f} 투입 {a['full_inj']}(실패 {a['full_inj_fail']})")
    os.makedirs("results", exist_ok=True)
    json.dump(res, open("results/index_cmp.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    # 덤: 이미 돌린 과거 구간 시험 기간의 지수 수익
    iw = {}
    for pat, key in (("sp1_*.json", "spx"), ("bear4_*.json", "kospi"), ("bear3_*.json", "kospi"), ("rand20_kospi_*.json", "kospi")):
        s = series[key]
        for f in glob.glob("results/" + pat):
            try:
                w = json.load(open(f, encoding="utf-8"))["window"]
            except Exception:
                continue
            if w in iw:
                continue
            a, z = pd.Timestamp(w[:10]), pd.Timestamp(w[-10:])
            x = s[(s.index >= a) & (s.index <= z)]
            if len(x) > 2:
                v = x.to_numpy()
                iw[w + "|" + key] = {"roc": float((v[-1] / v[0] - 1) * 100), "mdd": float(((v / np.maximum.accumulate(v)) - 1).min() * 100)}
    json.dump(iw, open("results/index_windows.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    # 간단한 표
    h = ["<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>index_cmp</title>",
         "<style>body{font:13px/1.5 -apple-system,'Noto Sans KR',sans-serif;margin:12px;background:#1E1F22;color:#E8ECF3}table{border-collapse:collapse;margin:6px 0 14px}"
         "td,th{border-bottom:1px solid #3a3b40;padding:3px 6px;text-align:right;white-space:nowrap}th{color:#9A9BA1;font-weight:500}.l{text-align:left}div{overflow-x:auto}</style>",
         "<h1 style='font-size:16px'>지수 200일선 시험</h1>"]
    for key, d in res["idx"].items():
        h.append(f"<h2 style='font-size:14px'>{d['name']} {d['from']}~{d['to']} · 5년 구간 {d['n_win']}개 · 200일선 아래 {d['below_pct']:.0f}%</h2><div><table><tr><th class='l'>규칙</th>"
                 "<th>평균</th><th>중앙</th><th>하위10%</th><th>최악</th><th>손실</th><th>MDD 평균</th><th>MDD 최악</th><th>보유보다↑</th><th>전체 기간</th><th>전체 MDD</th><th>투입(실패)</th></tr>")
        for r, a in d["agg"].items():
            h.append(f"<tr><td class='l'>{r}</td><td>{a['mean']:+.0f}</td><td>{a['med']:+.0f}</td><td>{a['p10']:+.0f}</td><td>{a['worst']:+.0f}</td><td>{a['loss']}</td>"
                     f"<td>{a['mdd']:.0f}</td><td>{a['mdd_worst']:.0f}</td><td>{a['beat_hold']}</td><td>{a['full']:+.0f}</td><td>{a['full_mdd']:.0f}</td><td>{a['full_inj']}({a['full_inj_fail']})</td></tr>")
        h.append("</table></div>")
    open("results/index_cmp.html", "w", encoding="utf-8").write("\n".join(h))


if __name__ == "__main__":
    import builtins
    import traceback
    _p = builtins.print
    builtins.print = lambda *a, **k: _p("::notice::" + " ".join(str(x) for x in a))   # 로그 대신 알림으로(로그 열기가 막혀 있어서)
    try:
        main()
    except Exception:
        for ln in traceback.format_exc().splitlines()[-8:]:
            _p("::error::" + ln)
        raise
