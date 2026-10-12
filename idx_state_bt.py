"""지수 상태 조건 시험 (2026-10-12, V1 규칙은 바꾸지 않는 별도 시험)

질문: 지수가 '하락 중'일 때 종목 매수 신호를 따르나? 지수가 '상승추세'일 때 매도·전량을 따르나?
지수 상태 = market_report.index_state (하락 중 > 바닥 다짐 > 상승추세 > 상승 중 쉬어감, 없으면 '없음'),
날마다 그날까지의 지수 종가로 다시 계산(미래 정보 없음). 국내 종목 = 코스피 상태, 해외 종목 = 나스닥 상태.

1) 신호표: V1 칸이 새로 켜진 날을 그날 지수 상태별로 나눠 5·20·60거래일 뒤 수익 — 기준선도 같은 상태의 날들(아무 날)로
2) 계좌(최종 규칙 portfolio.simulate_alloc held=(150,10,2), 손절 8%) 4가지:
   기본 / A 하락 중엔 매수 안 함 / B 상승추세엔 매도·전량 무시(손절은 그대로) / A+B
   (A·B는 그날 신호를 지운 것 — 상태가 끝난 뒤에도 신호가 켜져 있으면 그때 새 신호로 보고 사고판다)
3) 지수 자체에 V1(lab_bt.simulate, 1회 20%·연 추가금 10%, 시작 0%·50%) 같은 4가지 + 그냥 보유
기간 1·3·5년: 5년 시작일 기준 종목 목록으로 신호를 한 번 계산하고 기간마다 잘라서 씀(코스피 1·3년도 5년 전 상위 50 — 기존 백테스트와 조금 다름).

환경변수: IS_UNIVERSE = kospi | nasdaq | tickers, IS_VERSION, IS_MEMO
결과: results/idx_state_<대상>_<버전>.html·json (최신본은 idx_state_<대상>.html)
"""
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone

UNI = os.environ.get("IS_UNIVERSE", "kospi")
os.environ["BT_UNIVERSE"] = UNI
os.environ["BT_DAYS"] = "1260"
os.environ.setdefault("BT_TOP", "50")
os.environ["BT_OUT"] = "results/_tmp_idx_state"

import pandas as pd            # noqa: E402

import backtest as b           # noqa: E402
import lab_bt                  # noqa: E402
import market_report as m      # noqa: E402
import portfolio as pf         # noqa: E402

VERSION = os.environ.get("IS_VERSION", "").strip()
MEMO = os.environ.get("IS_MEMO", "").strip()
PERIODS = ((252, "1년"), (756, "3년"), (1260, "5년"))
STATES = ("down", "base", "up", "rest", "none")
ST_NAME = {"down": "하락 중", "base": "바닥 다짐", "up": "상승추세", "rest": "쉬어감", "none": "없음"}
CATS = (("v1·매수검토", "매수·바닥/눌림", "up"), ("불타기", "매수·추세", "up"), ("v1·매도검토", "매도·전량", "down"),
        ("v1·매수보류", "매수보류", "down"), ("v1·홀딩", "보유", "up"))
VARS = (("base", "기본 V1"), ("A", "A 하락 중 매수 안 함"), ("B", "B 상승추세 매도 무시"), ("AB", "A+B"))
E = html.escape


def index_series(fc, yc, bench=None, days=1260 + 70):
    """지수 하나: 날마다 상태 + 그 지수 자체의 V1 매수·매도 켜짐(그날까지 종가만)"""
    c = m._index_close(fc, yc).dropna()
    df = pd.DataFrame({"Close": c})
    out = []
    for t in range(max(300, len(c) - days), len(c)):
        sub = df.iloc[: t + 1]
        try:
            r = m.analyze_df(sub, fc, fc, light=True)
            st = m.index_state(r)
            r["rs60"] = m.rel_strength(sub["Close"], bench) if bench is not None else 0.0
            rv = m.review(r)
            out.append((c.index[t], float(c.iloc[t]), st["key"] if st else "none",
                        bool(rv.get("buyb") or rv.get("buy") or rv.get("up")), bool(rv.get("sell"))))
        except Exception:
            out.append((c.index[t], float(c.iloc[t]), "none", False, False))
    return pd.DataFrame(out, columns=["date", "close", "state", "buy", "sell"]).set_index("date")


def stats(v):
    v = [x for x in v if x is not None]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def main():
    t0 = time.time()
    spx = m._index_close("US500", "^GSPC")
    kos = index_series("KS11", "^KS11")
    ndx = index_series("IXIC", "^IXIC", bench=spx)
    print(f"지수 상태 계산 {time.time() - t0:.0f}초 · 코스피 {kos['state'].value_counts().to_dict()} · 나스닥 {ndx['state'].value_counts().to_dict()}", flush=True)
    st_of = {"kr": kos["state"], "us": ndx["state"]}

    tickers = b.load_universe()
    for t in tickers:
        b.bench_for(t["code"])
    res = {}
    for i, r in b.run_all(tickers):
        res[i] = r
        print(f"[{len(res)}/{len(tickers)}] {tickers[i]['name']} {'ok' if r[0] == 'ok' else r[1]}", flush=True)
    events, base_rows, states, failed = [], [], {}, []
    for i, t in enumerate(tickers):
        r = res.get(i)
        if not r or r[0] != "ok":
            failed.append(t["name"])
            continue
        ev, bs, stt = r[1]
        mk = "kr" if m.is_kr(t["code"]) else "us"
        stser = st_of[mk].reindex(stt.index, method="ffill").fillna("none")
        for e in ev:
            e["state"] = stser.get(e["date"], "none")
            e["mk"] = mk
        events += ev
        for d, bb in zip(stt.index, bs):
            base_rows.append({"date": d, "state": stser.get(d, "none"), **{h: bb.get(h) for h in (5, 20, 60)}})
        states[t["code"]] = {"close": stt["close"], "cats": stt["cats"], "mk": mk, "st": stser}

    ref = kos if UNI != "nasdaq" else ndx
    out = {"universe": UNI, "label": b.label_of().replace("시그널 백테스트", "지수 상태 시험"), "note": b.UNIVERSE_NOTE,
           "n": len(states), "failed": failed, "periods": {}}
    for P, pname in PERIODS:
        cut = ref.index[-min(P, len(ref))]
        # 1) 신호표
        B = {}
        for s_ in STATES + ("all",):
            rows = [x for x in base_rows if x["date"] >= cut and (s_ == "all" or x["state"] == s_)]
            B[s_] = {h: stats([x[h] for x in rows])[0] for h in (5, 20, 60)}
            B[s_]["days"] = len(rows)
        sig = {}
        for cat, nm, way in CATS:
            sig[cat] = {}
            for s_ in STATES + ("all",):
                ev = [e for e in events if e["cat"] == cat and e["date"] >= cut and (s_ == "all" or e["state"] == s_)]
                cell = {"n": len(ev)}
                for h in (5, 20, 60):
                    mu, _ = stats([e.get(f"r{h}") for e in ev])
                    cell[h] = None if mu is None or B[s_][h] is None else mu - B[s_][h]
                sig[cat][s_] = cell
        # 2) 계좌
        dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index if d >= cut}))
        acc = {}
        for vk, vname in VARS:
            sts = {}
            for c, s in states.items():
                cats = s["cats"].copy()
                stv = s["st"]
                if vk in ("A", "AB"):
                    cats = pd.Series([(set(x) - set(pf.ALLOC_BUY)) if stv.get(d) == "down" else x for d, x in cats.items()], index=cats.index)
                if vk in ("B", "AB"):
                    cats = pd.Series([(set(x) - set(pf.ALLOC_SELL)) if stv.get(d) == "up" else x for d, x in cats.items()], index=cats.index)
                if UNI == "tickers" and s["mk"] == "us":            # 섞인 목록: 해외는 하루 늦춰 '당일 체결'로(live.py와 같은 방식)
                    cats = cats.shift(1).apply(lambda x: x if isinstance(x, (set, frozenset)) else set())
                sts[c] = {"close": s["close"], "cats": cats}
            same = UNI in ("kospi", "tickers")
            eq, trades, cash, open_n, _ = pf.simulate_alloc(sts, dates, 0, 0, same_day=same, held=(150, 10, 2))
            acc[vk] = pf.metrics(eq, trades, cash, open_n)
        # 균등 보유·지수 보유(참고)
        px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
        eqw = (px / px.bfill().iloc[0]).mean(axis=1)
        acc["eqw"] = pf.metrics(eqw)
        acc["idx"] = pf.metrics(ref["close"][ref.index >= cut])
        # 3) 지수 자체에 V1
        idx_run = {}
        for key, S, same in (("kospi", kos, True), ("ndx", ndx, False)):
            S2 = S[S.index >= cut]
            ds = [f"{d:%Y-%m-%d}" for d in S2.index]
            cl, st = list(S2["close"]), list(S2["state"])
            runs = {"hold": lab_bt.simulate(ds, cl, [False] * len(ds), [False] * len(ds), start=1.0, same_day=same, stop=False)}
            for vk, _ in VARS:
                buy = [bb and not (vk in ("A", "AB") and s_ == "down") for bb, s_ in zip(S2["buy"], st)]
                sell = [ss and not (vk in ("B", "AB") and s_ == "up") for ss, s_ in zip(S2["sell"], st)]
                for start in (0.0, 0.5):
                    runs[f"{vk}_{int(start * 100)}"] = lab_bt.simulate(ds, cl, buy, sell, start=start, buy_pct=20, cap=10, same_day=same)
            idx_run[key] = {k: {x: v[x] for x in ("twr", "mdd", "roc", "irr", "invested", "n_trades", "win", "cash")} for k, v in runs.items()}
            idx_run[key]["state_days"] = pd.Series(st).value_counts().to_dict()
        out["periods"][pname] = {"cut": f"{cut:%Y-%m-%d}", "B": B, "sig": sig, "acc": acc, "idx": idx_run}
        print(f"== {pname} ({cut:%Y-%m-%d}~) 계좌: " + " · ".join(f"{vn} {acc[vk]['tot']:+.1f}%/{acc[vk]['mdd']:.1f}%" for vk, vn in VARS)
              + f" · 균등 {acc['eqw']['tot']:+.1f}% · 지수 {acc['idx']['tot']:+.1f}%", flush=True)
    out["secs"] = round(time.time() - t0)
    out["run"] = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    out["memo"] = MEMO
    write(out)


def f(v, d=1, pct=True):
    if v is None:
        return "-"
    return f"{v:+.{d}f}{'%p' if pct else ''}"


def cls(v, way="up", tol=1.0):
    if v is None:
        return ""
    good = v > tol if way == "up" else v < -tol
    bad = v < -tol if way == "up" else v > tol
    return "ok" if good else "bad" if bad else ""


def write(o):
    os.makedirs("results", exist_ok=True)
    name = f"idx_state_{o['universe']}" + (f"_{VERSION}" if VERSION else "")
    css = """body{font:13px/1.5 -apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif;margin:16px;color:#222;background:#fff;max-width:1100px}
h1{font-size:18px}h2{font-size:15px;margin-top:26px}h3{font-size:13px;margin:16px 0 4px}table{border-collapse:collapse;margin:6px 0;font-size:12px}
td,th{border:1px solid #ddd;padding:3px 6px;text-align:right;white-space:nowrap}th{background:#f4f4f4}td.l,th.l{text-align:left}
.ok{background:#e6f6ea}.bad{background:#f9d0d0}.note{color:#666;font-size:12px}small{color:#888}
@media (prefers-color-scheme:dark){body{background:#16171a;color:#ddd}th{background:#24262b}td,th{border-color:#333}.ok{background:#1d3a26}.bad{background:#4a1e1e}.note{color:#999}}"""
    h = [f"<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{E(o['label'])}</title><style>{css}</style>",
         f"<h1>{E(o['label'])}</h1><p class='note'>실행 {o['run']} KST · {o['secs'] // 60}분 · 종목 {o['n']}개{' · 실패 ' + ', '.join(o['failed']) if o['failed'] else ''} · {E(o['note'])}"
         f"{' · 메모: ' + E(o['memo']) if o['memo'] else ''}</p>",
         "<p class='note'>지수 상태는 날마다 그날까지의 지수 종가로 계산(국내 종목 = 코스피, 해외 = 나스닥). 신호표 숫자 = 신호 뒤 수익 − <b>같은 지수 상태의 아무 날</b> 수익(%p), "
         "초록 = 기대 방향으로 1%p 이상(매수류는 더 오름, 매도류는 덜 오름), 빨강 = 반대. 괄호 = 건수.<br>계좌 = 최종 규칙(보유 종목 수 기준 상한 min(10%, 150÷종목 수)·2회 분할, 손절 8%). "
         "A = 지수 하락 중인 날 매수 신호 무시, B = 지수 상승추세인 날 매도·전량 무시(손절은 유지). 신호가 그 뒤에도 켜져 있으면 그때 사고판다.</p>"]
    for pname, P in o["periods"].items():
        h.append(f"<h2>{pname} ({P['cut']}~)</h2><h3>계좌</h3><table><tr><th class='l'>방식</th><th>총수익</th><th>연평균</th><th>최대 하락</th><th>매매</th><th>승률</th><th>현금 비중</th></tr>")
        base = P["acc"]["base"]["tot"]
        for vk, vn in VARS + (("eqw", "같은 종목 균등 보유"), ("idx", "지수 보유")):
            a = P["acc"][vk]
            c_ = "" if vk in ("base", "eqw", "idx") else ("ok" if a["tot"] > base + 1 else "bad" if a["tot"] < base - 1 else "")
            h.append(f"<tr><td class='l'>{vn}</td><td class='{c_}'>{a['tot']:+.1f}%</td><td>{a['cagr']:+.1f}%</td><td>{a['mdd']:.1f}%</td>"
                     f"<td>{a.get('n', '-')}</td><td>{'-' if a.get('win') is None else f'{a['win']:.0f}%'}</td><td>{'-' if a.get('cash') is None else f'{a['cash']:.0f}%'}</td></tr>")
        h.append("</table><h3>신호표 · 20거래일 뒤 (60거래일 뒤)</h3><table><tr><th class='l'>V1 칸</th>" +
                 "".join(f"<th>{ST_NAME.get(s_, '전체')}<br><small>{P['B'][s_]['days']}일</small></th>" for s_ in STATES + ("all",)) + "</tr>")
        for cat, nm, way in CATS:
            row = f"<tr><td class='l'>{nm}</td>"
            for s_ in STATES + ("all",):
                c = P["sig"][cat][s_]
                row += f"<td class='{cls(c[20], way)}'>{f(c[20])} <small>({f(c[60], 1)}) · {c['n']}</small></td>" if c["n"] else "<td>-</td>"
            h.append(row + "</tr>")
        h.append("</table><h3>지수 자체에 V1 (1회 20% · 연 추가금 10%)</h3><table><tr><th class='l'>지수 · 방식</th><th>시작 0% 총수익</th><th>최대 하락</th><th>매매</th>"
                 "<th>시작 50% 총수익</th><th>최대 하락</th></tr>")
        for key, nm in (("kospi", "코스피"), ("ndx", "나스닥")):
            R = P["idx"][key]
            h.append(f"<tr><td class='l'>{nm} 그냥 보유</td><td colspan='2'>{R['hold']['twr']:+.1f}% / {R['hold']['mdd']:.1f}%</td><td>-</td><td colspan='2'></td></tr>")
            for vk, vn in VARS:
                a0, a5 = R[f"{vk}_0"], R[f"{vk}_50"]
                h.append(f"<tr><td class='l'>{nm} · {vn}</td><td>{a0['twr']:+.1f}%</td><td>{a0['mdd']:.1f}%</td><td>{a0['n_trades']}</td>"
                         f"<td>{a5['twr']:+.1f}%</td><td>{a5['mdd']:.1f}%</td></tr>")
            sd = R["state_days"]
            h.append(f"<tr><td class='l' colspan='6'><small>{nm} 상태 일수: " + " · ".join(f"{ST_NAME[k]} {sd.get(k, 0)}" for k in STATES) + "</small></td></tr>")
        h.append("</table>")
    page = "\n".join(h)
    for nm in [name] + ([f"idx_state_{o['universe']}"] if VERSION else []):
        with open(f"results/{nm}.html", "w", encoding="utf-8") as fp:
            fp.write(page)
        with open(f"results/{nm}_data.json", "w", encoding="utf-8") as fp:
            json.dump(o, fp, ensure_ascii=False, default=str)
    print(f"results/{name}.html")


if __name__ == "__main__":
    main()
