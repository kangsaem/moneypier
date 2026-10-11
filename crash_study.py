"""
폭락장 표시 뒤 성과: 아래 세 조건 중 하나라도 켜진 날(= 리포트의 '폭락장')이 그 뒤 지수에 좋은 매수 시기였는지 본다.
  ① 지수(코스피·S&P500·나스닥) 52주 고점 대비 -20% 이하
  ② VIX 30 이상
  ③ 시장 바닥 지표 2개 이상 — 리포트의 5개 중 '내 종목 RSI' 항목은 과거 재현이 안 돼서 빼고 4개로 계산
     (코스피 50일 이격도 90 이하 또는 과거 최소권 / 코스피 RSI 35 이하 / 코스피 고점 -20% + 20거래일 신저점 없음 / VIX 30 이상)
'처음 켜진 날' = 꺼진 지 20거래일 이상 지난 뒤 다시 켜진 날. 그날 종가에 지수를 샀다면 1·3·6·12개월(21·63·126·252거래일) 뒤 수익률을
같은 지수의 '아무 날이나 샀을 때' 평균과 비교. 결과: results/crash_study.html (+ .json)
"""
import contextlib
import html
import io
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

import market_report as m

START = "2000-01-01"
HZ = (21, 63, 126, 252)
GAP = 20
OUT = os.environ.get("CRASH_OUT", "results/crash_study")
IDX = (("kospi", "코스피", "KS11", "^KS11"), ("spx", "S&P500", "US500", "^GSPC"), ("ndx", "나스닥", "IXIC", "^IXIC"))


def close(fc, yc):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s = m.fdr.DataReader(fc, START)["Close"].dropna()
        if len(s) > 1000:
            return s
    except Exception:
        pass
    import yfinance as yf
    s = yf.Ticker(yc).history(start=START)["Close"].dropna()
    s.index = s.index.tz_localize(None)
    return s


def flags():
    px = {k: close(fc, yc) for k, _, fc, yc in IDX}
    vix = close("VIX", "^VIX")
    days = px["kospi"].index.union(px["spx"].index)
    f = pd.DataFrame(index=days)
    for k, nm, _, _ in IDX:
        s = px[k].reindex(days).ffill()
        f[f"dd_{k}"] = s / s.rolling(250, min_periods=60).max() - 1 <= -0.20
    v = vix.reindex(days).ffill()
    f["vix"] = v >= 30
    ks = px["kospi"]
    disp = ks / ks.rolling(50).mean() * 100
    dmin = disp.expanding().min()
    r = m.rsi(ks)
    hi = ks.rolling(250, min_periods=60).max()
    lo_age = ks.rolling(250, min_periods=60).apply(lambda w: len(w) - 1 - w.argmin(), raw=True)
    b = pd.DataFrame({"disp": (disp <= 90) | (disp <= dmin * m.NEAR_MIN), "rsi": r <= 35,
                      "dd": (ks / hi - 1 <= -0.20) & (lo_age >= 20), "vix": (v.reindex(ks.index).ffill() >= 30)})
    f["bottom2"] = (b.sum(axis=1) >= 2).reindex(days).ffill().fillna(False)
    f["any"] = f[[c for c in f.columns if c.startswith("dd_")] + ["vix", "bottom2"]].any(axis=1)
    return f.fillna(False), px


def starts(on):
    out, last = [], -10 ** 9
    on = on.to_numpy()
    for i, x in enumerate(on):
        if x and (i == 0 or not on[i - 1]):
            if i - last > GAP:
                out.append(i)
        if x:
            last = i
    return out


def fwd(s, days, idxs, h):
    s = s.reindex(days).ffill()
    v = []
    for i in idxs:
        if i + h < len(s) and pd.notna(s.iloc[i]) and pd.notna(s.iloc[i + h]):
            v.append((s.iloc[i + h] / s.iloc[i] - 1) * 100)
    return v


def main():
    f, px = flags()
    days = f.index
    conds = [("any", "폭락장 (셋 중 하나)"), ("dd_kospi", "코스피 고점 -20%"), ("dd_spx", "S&P500 고점 -20%"),
             ("dd_ndx", "나스닥 고점 -20%"), ("vix", "VIX 30 이상"), ("bottom2", "바닥 지표 2개 이상")]
    res = {"since": f"{days[0]:%Y-%m-%d}", "to": f"{days[-1]:%Y-%m-%d}", "rows": [], "events": []}
    base = {k: {h: fwd(px[k], days, range(len(days)), h) for h in HZ} for k, _, _, _ in IDX}
    for key, label in conds:
        st = starts(f[key])
        on_days = int(f[key].sum())
        row = {"key": key, "label": label, "n": len(st), "days": on_days, "pct_days": on_days / len(days) * 100, "idx": {}}
        for k, nm, _, _ in IDX:
            row["idx"][k] = {}
            for h in HZ:
                ev = fwd(px[k], days, st, h)
                bl = base[k][h]
                row["idx"][k][h] = {"avg": sum(ev) / len(ev) if ev else None, "win": (sum(1 for x in ev if x > 0) / len(ev) * 100) if ev else None,
                                    "base": sum(bl) / len(bl) if bl else None, "n": len(ev)}
        res["rows"].append(row)
    for i in starts(f["any"]):
        d = days[i]
        why = [lb for k, lb in conds[1:] if f[k].iloc[i]]
        res["events"].append({"date": f"{d:%Y-%m-%d}", "why": why,
                              **{f"{k}_{h}": (lambda v: v[0] if v else None)(fwd(px[k], days, [i], h)) for k, _, _, _ in IDX for h in (63, 252)}})
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT + ".json", "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=1)
    write_html(res)
    print(f"{OUT}.html 생성 완료 (폭락장 시작 {len(res['events'])}번)")


def write_html(res):
    E = lambda x: html.escape(str(x))
    pf = lambda v: "-" if v is None else f"{v:+.1f}%"
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    head = "".join(f'<th colspan="{len(HZ)}">{E(nm)} 지수 매수 시</th>' for _, nm, _, _ in IDX)
    sub = "".join(f"<th>{h // 21}개월</th>" for _ in IDX for h in HZ)
    trs = ""
    for r in res["rows"]:
        cells = ""
        for k, _, _, _ in IDX:
            for h in HZ:
                c = r["idx"][k][h]
                if c["avg"] is None:
                    cells += "<td>-</td>"
                    continue
                gap = c["avg"] - c["base"]
                cls = "up" if gap > 1 else "dn" if gap < -1 else ""
                cells += f'<td class="{cls}">{pf(c["avg"])}<br><small>평소 {pf(c["base"])} · 오름 {c["win"]:.0f}%</small></td>'
        trs += (f'<tr><td class="c"><b>{E(r["label"])}</b><br><small>시작 {r["n"]}번 · 켜진 날 {r["pct_days"]:.0f}%</small></td>{cells}</tr>')
    ev = "".join(f'<tr><td class="c">{E(e["date"])}</td><td class="c">{E(" · ".join(e["why"]))}</td>'
                 + "".join(f'<td>{pf(e.get(f"{k}_{h}"))}</td>' for k, _, _, _ in IDX for h in (63, 252)) + "</tr>"
                 for e in reversed(res["events"]))
    evh = "".join(f"<th>{E(nm)} 3개월</th><th>{E(nm)} 12개월</th>" for _, nm, _, _ in IDX)
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>폭락장 표시 뒤 성과</title><style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --up:#d92d20; --dn:#1d5fd1; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38; --up:#ff6b5e; --dn:#6ea2ff; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:1100px; line-height:1.5; }}
h1 {{ font-size:1.25rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:18px 0 6px; }} .t, .note, small {{ color:var(--mut); font-size:.8rem; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.8rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c {{ text-align:left; white-space:normal; }} td.up {{ color:var(--up); }} td.dn {{ color:var(--dn); }}
</style></head><body>
<h1>폭락장 표시 뒤 성과</h1>
<div class="t">{E(res["since"])} ~ {E(res["to"])} · 계산 {kst:%Y-%m-%d %H:%M} KST · <a href="backtests.html">백테스트 목록</a></div>
<p class="note">폭락장 = 지수(코스피·S&P500·나스닥) 52주 고점 대비 -20% 이하, VIX 30 이상, 시장 바닥 지표 2개 이상(과거 재현이 안 되는 '내 종목 RSI'는 빼고 4개 중) 중 하나라도 켜진 날.
'시작' = 꺼진 지 {GAP}거래일 넘게 지나 다시 켜진 날. 그날 종가에 지수를 샀을 때 n개월 뒤 수익(배당 제외) — 아래 작은 글씨는 아무 날이나 샀을 때의 평균(평소)과 오른 비율.
평소보다 1%p 이상 높으면 빨강, 낮으면 파랑. 시작 횟수가 적으면(10번 미만) 우연일 수 있음.</p>
<h2>조건별</h2><div class="wrap"><table><tr><th class="c" rowspan="2">조건</th>{head}</tr><tr>{sub}</tr>{trs}</table></div>
<h2>폭락장 시작일 목록 (최근 순)</h2><div class="wrap"><table><tr><th class="c">날짜</th><th class="c">켜진 조건</th>{evh}</tr>{ev}</table></div>
</body></html>"""
    with open(OUT + ".html", "w", encoding="utf-8") as fp:
        fp.write(page)


if __name__ == "__main__":
    main()
