"""
신호 기록: 리포트를 만들 때마다 그날 종목별 리포트 칸(매수검토·매도검토·매수보류·홀딩·상승중·반등대기·하락중)을
history/<날짜>_<kr|us>.json 으로 남기고(daily.yml이 저장소에 커밋), 지난 기록을 모아 docs/history.html 을 만든다.
- 20·60거래일 뒤 수익률은 지금 받아 둔 일봉으로 계산(그날 종가 기준). 기준선 = 같은 날 기록된 전 종목 평균.
- 월별 요약: 칸별 건수·20일 뒤 평균(기준선 대비) + 그 달 내 종목(균등) vs 코스피 vs 나스닥 수익률.
- 머니파이(브라우저)도 같은 형식(FORMAT 1)으로 저장 → 나중에 서로 비교·합치기 쉬움.
"""
import glob
import html
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

import market_report as m

DIR = os.environ.get("HISTORY_DIR", "history")
FORMAT = 1
HORIZONS = (20, 60)
E = html.escape


# ---------------------------------------------------------------- 저장
def record(results, msig, session, now=None):
    """오늘 기록 dict (FORMAT 1). session = 'kr'(국장 마감 리포트) | 'us'(미장 마감 리포트)"""
    kst = now or (datetime.now(timezone.utc) + timedelta(hours=9))
    items = []
    for r in results:
        rv = r.get("review") or m.review(r)
        groups = [{"g": nm + ("·" + rv[k]["level"] if rv[k].get("level") else ""), "s": rv[k]["score"], "why": rv[k]["why"]}
                  for k, nm, _ in m.REVIEW if rv.get(k)]
        items.append({"code": r["code"], "name": r["name"], "asof": f"{r['last']:%Y-%m-%d}", "close": round(r["close"], 4),
                      "rsi": round(r["rsi"], 1), "disp": round(r["disp"]["cur"], 1) if r.get("disp") else None,
                      "rs60": None if r.get("rs60") is None else round(r["rs60"], 1), "groups": groups})
    mk = {k: {"score": g["score"], "total": len(g["items"]), "label": g["label"]} for k, g in (msig or {}).items()
          if isinstance(g, dict) and "items" in g}
    return {"format": FORMAT, "logic": "v1", "date": f"{kst:%Y-%m-%d}", "session": session, "time": f"{kst:%Y-%m-%d %H:%M}",
            "market": mk, "items": items}


def save(rec):
    os.makedirs(DIR, exist_ok=True)
    path = os.path.join(DIR, f"{rec['date']}_{rec['session']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=0)
    return path


def load_all():
    recs = []
    for f in sorted(glob.glob(os.path.join(DIR, "*.json"))):
        try:
            recs.append(json.load(open(f, encoding="utf-8")))
        except Exception:
            pass
    return recs


# ---------------------------------------------------------------- 계산
def fwd(c, asof, h):
    """asof(그 종목 마지막 봉 날짜) 종가 대비 h거래일 뒤 수익률(%). 아직 안 지났으면 None"""
    if c is None or not len(c):
        return None
    i = c.index.searchsorted(pd.Timestamp(asof), side="right") - 1
    if i < 0 or i + h >= len(c):
        return None
    return float((c.iloc[i + h] / c.iloc[i] - 1) * 100)


OLD_NAME = {"추격매수 주의": "매수보류", "홀딩 유지·순항": "홀딩·순항", "홀딩 유지·눌림": "홀딩·눌림", "대기": "반등대기", "하락-대기": "하락중"}


def rows_of(recs, closes):
    """기록 → 줄 목록. 같은 종목·같은 asof는 마지막 기록 하나만(국장·미장 리포트가 같은 봉을 두 번 기록하는 경우)"""
    by = {}
    for rec in recs:
        for it in rec.get("items", []):
            by[(it["code"], it["asof"])] = (rec, it)
    rows = []
    for (code, asof), (rec, it) in by.items():
        c = closes.get(code)
        groups = [dict(x, g=OLD_NAME.get(x["g"], x["g"])) for x in it.get("groups", [])]    # 2026-10-11 이전 기록은 옛 이름
        row = {"date": asof, "code": code, "name": it["name"], "groups": groups, "close": it.get("close")}
        for h in HORIZONS:
            row[h] = fwd(c, asof, h)
        rows.append(row)
    base = {}
    for h in HORIZONS:              # 기준선: 같은 날(asof) 기록된 전 종목 평균
        df = pd.DataFrame([(r["date"], r[h]) for r in rows if r[h] is not None], columns=["d", "v"])
        base[h] = df.groupby("d")["v"].mean().to_dict() if len(df) else {}
    for r in rows:
        for h in HORIZONS:
            b = base[h].get(r["date"])
            r[f"x{h}"] = None if r[h] is None or b is None else r[h] - b
    return sorted(rows, key=lambda r: (r["date"], r["name"]), reverse=True)


def month_ret(c, ym):
    if c is None or not len(c):
        return None
    prev = c[c.index < pd.Timestamp(ym + "-01")]
    cur = c[(c.index >= pd.Timestamp(ym + "-01")) & (c.index < pd.Timestamp(ym + "-01") + pd.offsets.MonthBegin(1))]
    if not len(cur) or not len(prev):
        return None
    return float((cur.iloc[-1] / prev.iloc[-1] - 1) * 100)


# ---------------------------------------------------------------- 페이지
GROUP_ORDER = ["매수검토", "매도검토", "매수보류", "홀딩·순항", "홀딩·눌림", "상승중", "반등대기", "하락중"]
GROUP_WAY = {"매수검토": 1, "매도검토": -1, "매수보류": -1, "홀딩·순항": 1, "홀딩·눌림": 1, "상승중": 1, "반등대기": 1, "하락중": 1}


def _p(v, d=1):
    return "-" if v is None else f"{v:+.{d}f}%"


def _cls(v, way=1):
    if v is None:
        return "mut"
    return "ok" if v * way > 0 else "bad" if v * way < 0 else ""


def build(results, out="docs/history.html"):
    recs = load_all()
    closes = {r["code"]: r.get("_c") for r in results}
    rows = rows_of(recs, closes)
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    idx = {}
    for nm, fc, yc in (("코스피", "KS11", "^KS11"), ("나스닥", "IXIC", "^IXIC")):
        try:
            idx[nm] = m._index_close(fc, yc)
        except Exception:
            idx[nm] = None
    # 월별 요약
    months = sorted({r["date"][:7] for r in rows}, reverse=True)
    msum = ""
    for ym in months:
        mr = [r for r in rows if r["date"].startswith(ym)]
        cells = ""
        for g in GROUP_ORDER:
            hit = [r for r in mr if any(x["g"] == g for x in r["groups"])]
            xs = [r["x20"] for r in hit if r["x20"] is not None]
            av = sum(xs) / len(xs) if xs else None
            cells += (f'<td>{len(hit) or "-"}</td><td class="{_cls(av, GROUP_WAY[g])}">{_p(av)}</td>' if hit else
                      '<td class="mut">-</td><td class="mut">-</td>')
        mine = [x for x in (month_ret(closes.get(code), ym) for code in {r["code"] for r in mr}) if x is not None]
        my = sum(mine) / len(mine) if mine else None
        ks, nq = month_ret(idx.get("코스피"), ym), month_ret(idx.get("나스닥"), ym)
        msum += (f'<tr><td class="c"><b>{ym}</b></td>{cells}<td class="g"><b>{_p(my)}</b></td><td>{_p(ks)}</td><td>{_p(nq)}</td></tr>')
    ghead = "".join(f'<th colspan="2" class="g">{E(g)}</th>' for g in GROUP_ORDER)
    gsub = "".join('<th class="g">건수</th><th>20일 뒤</th>' for _ in GROUP_ORDER)
    # 칸별 기록(신호가 있는 줄만, 최근 순)
    det = ""
    for r in rows:
        for x in r["groups"]:
            if x["g"] not in GROUP_WAY:
                continue
            w = GROUP_WAY[x["g"]]
            det += (f'<tr data-g="{E(x["g"])}"><td class="c">{r["date"][2:]}</td><td class="c"><b>{E(r["name"])}</b> <small>{E(r["code"])}</small></td>'
                    f'<td class="c"><span class="tag t{GROUP_ORDER.index(x["g"])}">{E(x["g"])}{" " + "●" * x["s"] if x["s"] else ""}</span></td>'
                    f'<td class="c why">{E(" · ".join(x.get("why", [])))}</td>'
                    f'<td class="{_cls(r[20], w)}">{_p(r[20])}</td><td class="{_cls(r["x20"], w)}">{_p(r["x20"])}</td>'
                    f'<td class="{_cls(r[60], w)}">{_p(r[60])}</td><td class="{_cls(r["x60"], w)}">{_p(r["x60"])}</td></tr>')
    days = sorted({r["date"] for r in rows})
    filt = "".join(f'<button data-g="{E(g)}">{E(g)}</button>' for g in GROUP_ORDER)
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>신호 기록</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --ok:#0f766e; --okbg:#d9f2ee; --bad:#d92d20; --badbg:#fdecea;
  --buy:#d92d20; --sell:#1d5fd1; --amber:#c27803; --okfg:#3c7a13; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38; --ok:#4fd1c0; --okbg:#10302c;
  --bad:#ff6b5e; --badbg:#3a1d1a; --buy:#ff6b5e; --sell:#6ea2ff; --amber:#f0b44a; --okfg:#9fd774; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:1100px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:20px 0 6px; }} .t, .note {{ color:var(--mut); font-size:.8rem; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.8rem; }} th, td {{ padding:5px 7px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} td.c, th.c {{ text-align:left; }} td.why {{ white-space:normal; min-width:180px; color:var(--mut); font-size:.75rem; }}
.g {{ border-left:1px solid var(--line); }} td.ok {{ color:var(--ok); font-weight:600; }} td.bad {{ color:var(--bad); }} td.mut {{ color:var(--mut); }}
small {{ color:var(--mut); }} a {{ color:inherit; }}
.tag {{ font-size:.72rem; padding:1px 7px; border-radius:6px; border:1px solid var(--line); font-weight:600; }}
.t0 {{ color:var(--buy); border-color:var(--buy); }} .t1 {{ color:var(--sell); border-color:var(--sell); }} .t2 {{ color:var(--amber); border-color:var(--amber); }}
.t3, .t4 {{ color:var(--okfg); border-color:var(--okfg); }} .t5 {{ color:var(--buy); }} .t6, .t7 {{ color:var(--mut); }}
.flt {{ display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }} .flt button {{ font:inherit; font-size:.78rem; padding:3px 10px; border-radius:99px;
  border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }} .flt button.on {{ background:var(--fg); color:var(--bg); }}
</style></head><body>
<h1>신호 기록</h1>
<div class="t">리포트가 만들어질 때마다 그날 종목별 칸을 저장 · 기록 {len(days)}일({days[0] if days else '-'} ~ {days[-1] if days else '-'}) · 갱신 {kst:%Y-%m-%d %H:%M} KST · <a href="./">리포트로</a></div>
<p class="note">20·60일 뒤 = 그날 종가 대비 20·60거래일 뒤 수익률. 기준선 대비 = 같은 날 기록된 내 종목 전체 평균보다 몇 %p 나았나.
초록 = 칸의 뜻대로 맞음(매수검토·홀딩·상승중은 오르면, 매도검토·매수보류는 덜 오르거나 내리면), 빨강 = 반대. 아직 기간이 안 지났으면 '-'.</p>
<h2>월별 요약 <small>(칸별 건수 · 20일 뒤 기준선 대비 평균, 그 달 수익률)</small></h2>
<div class="wrap"><table>
<tr><th class="c" rowspan="2">월</th>{ghead}<th class="g" rowspan="2">내 종목<br>(균등)</th><th rowspan="2">코스피</th><th rowspan="2">나스닥</th></tr>
<tr>{gsub}</tr>
{msum or '<tr><td colspan="20" class="mut">아직 기록 없음</td></tr>'}</table></div>
<p class="note">머니파이 월별 메모에 옮겨 적을 때: 이 표의 그 달 줄(매수검토·매도검토가 맞았나, 내 종목 vs 코스피·나스닥) + 규칙 외 매매 횟수.</p>
<h2>칸별 기록</h2>
<div class="flt" id="flt"><button data-g="" class="on">전체</button>{filt}</div>
<div class="wrap"><table id="tb">
<tr><th class="c">날짜</th><th class="c">종목</th><th class="c">칸</th><th class="c">근거</th><th class="g">20일 뒤</th><th>기준선 대비</th><th class="g">60일 뒤</th><th>기준선 대비</th></tr>
{det or '<tr><td colspan="8" class="mut">아직 기록 없음</td></tr>'}</table></div>
<script>
const flt = document.getElementById('flt'), tb = document.getElementById('tb');
flt.addEventListener('click', e => {{ const b = e.target.closest('button'); if (!b) return;
  [...flt.children].forEach(x => x.classList.toggle('on', x === b));
  tb.querySelectorAll('tr[data-g]').forEach(tr => tr.style.display = !b.dataset.g || tr.dataset.g === b.dataset.g ? '' : 'none'); }});
</script>
</body></html>"""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(os.path.dirname(out), "history_all.json"), "w", encoding="utf-8") as f:   # 머니파이 가져오기용(모든 기록)
        json.dump(recs, f, ensure_ascii=False)
    return out
