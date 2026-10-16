"""
V1 실전 가상계좌: 2026-10-12부터 리포트가 매일 남기는 신호 기록(history/*.json)만으로 v1 규칙을 그대로 따라 한 가상 계좌.
백테스트와 달리 '그날 뜬 신호'만 쓰므로 결과를 미리 아는 효과가 없다.
  - 매수: 매수·바닥 / 매수·눌림 / 매수·추세 신호가 새로 켜지면 — 국장·미장 모두 신호 다음 거래일 종가(2026-10-14~: 국장 판정 = 20:00 확정 일봉, 매매 = 다음 날 장중)
  - 금액: 종목당 상한 = 계좌 평가액 × min(10%, 150 ÷ 보유 종목 수(안 가진 종목이면 +1)), 신호 한 번에 상한의 1/2 (2회 분할)
  - 매도: 매도·전량 신호(다음 거래일 종가) 또는 손절(평균 매수가 대비 종가 -8% 이하가 이틀 연속) → 전량
  - 비교: 내 종목 그냥 보유(시작일·매년 첫 거래일에 그때 종목을 똑같이 나눠 삼) / 코스피 / 나스닥
계산은 portfolio.simulate_alloc(백테스트의 '최종규칙 2회'와 같은 함수). 결과: docs/live.html + docs/live.json
"""
import html
import json
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

import history
import market_report as m
import portfolio as pf

START = os.environ.get("LIVE_START", "2026-10-12")
HELD = (150, 10, 2)
BUY_G = ("매수·바닥", "매수·눌림", "매수·추세")
SELL_G = ("매도·전량",)
LINES = (("v1", "V1 실전"), ("hold", "내 종목 그냥 보유"), ("kospi", "코스피"), ("ndx", "나스닥"))
COLORS = {"v1": "#e0457b", "hold": "#f29a3d", "kospi": "#3b82c4", "ndx": "#14a37f"}
E = html.escape


def _signals(recs):
    """(code) → {날짜: set(카테고리)}, 이름, 가장 최근 기록 종가들. 같은 종목·같은 봉은 마지막 기록 하나"""
    sig, names, rec_close = {}, {}, {}
    for rec in recs:
        for it in rec.get("items", []):
            gs = {history.new_name(x) for x in it.get("groups", [])}
            cat = set()
            if gs & set(SELL_G):
                cat.add(pf.ALLOC_SELL[0])
            elif gs & set(BUY_G):
                cat.add(pf.ALLOC_BUY[0])
            d = pd.Timestamp(it["asof"])
            sig.setdefault(it["code"], {})[d] = cat
            names[it["code"]] = it["name"]
            if it.get("close"):
                rec_close.setdefault(it["code"], {})[d] = float(it["close"])
    return sig, names, rec_close


def _hold_nav(px, members, dates):
    """시작일(과 매년 첫 거래일)에 그때 종목을 똑같이 나눠 사서 들고만 있는 경우의 평가액(시작 1)"""
    nav, val, w, last_year = [], 1.0, {}, None
    prev_i = None
    for i, d in enumerate(dates):
        if last_year != d.year:                                   # 새로 나눠 삼(어제 종가 기준 평가액으로)
            last_year = d.year
            codes = [c for c in members(d) if c in px and pd.notna(px[c][i])]
            base_i = i if prev_i is None else prev_i
            w = {c: val / len(codes) / px[c][base_i] for c in codes if pd.notna(px[c][base_i]) and px[c][base_i] > 0} if codes else {}
        if w:
            val = sum(sh * px[c][i] for c, sh in w.items() if pd.notna(px[c][i]))
        nav.append(val)
        prev_i = i
    return nav


def compute(results, recs=None, idx=None):
    recs = recs if recs is not None else history.load_all()
    recs = [r for r in recs if r.get("date", "") >= START]
    if not recs:
        return None
    sig, names, rec_close = _signals(recs)
    closes = {r["code"]: r.get("_c") for r in results if r.get("_c") is not None}
    start = pd.Timestamp(START)
    # 시세: 지금 리포트의 일봉, 목록에서 빠진 종목은 기록된 종가로
    ser = {}
    for c in sig:
        s = closes.get(c)
        if s is None or not len(s):
            s = pd.Series(rec_close.get(c, {})).sort_index()
        s = s[s.index >= start - pd.Timedelta(days=10)]
        if len(s):
            ser[c] = s
    if not ser:
        return None
    dates = sorted({d for s in ser.values() for d in s.index if d >= start})
    if not dates:
        return None
    before = [d for s in ser.values() for d in s.index if d < start]
    if before:                                                     # 시작 전날을 기준점(0%)으로 — 첫날 신호도 체결되게
        dates = [max(before)] + dates
    dates = pd.DatetimeIndex(dates)
    # 목록에서 빠진 구간(2번 이상 연속 기록에 없음): 빠지기 전 마지막 기록일 종가에 전량 매도, 그 사이는 신호 없음, 다시 들어오면 신호대로
    gp = history.gaps(recs)
    gone_at = {c: {pd.Timestamp(st) for st, _ in gl} for c, gl in gp.items()}
    states = {}
    for c, s in ser.items():
        own = s.index[s.index >= start]
        cats = pd.Series({d: sig[c].get(d) for d in own}, dtype=object)
        cats = cats.ffill(limit=3)                                 # 기록이 빠진 날(실패 등)은 전날 칸 유지(최대 3일)
        cats = cats.shift(1)                                       # 신호 다음 거래일 종가에 체결(국장도 2026-10-14부터 — 20:00 확정 일봉으로 판정, 다음 날 장중 매매)
        cats = cats.reindex(cats.index.union(dates)).ffill(limit=5).reindex(dates)   # 다른 시장만 쉬는 날은 칸 유지
        cats = cats.apply(lambda x: x if isinstance(x, set) else set())
        for st, en in gp.get(c, []):                               # 목록에서 빠짐 = 팔고 뺀 것 → 마지막 기록일 종가에 전량 매도
            st = pd.Timestamp(st)
            if st not in cats.index:
                continue
            cats = cats.copy()
            cats.loc[st] = {pf.ALLOC_SELL[0]}
            mask = (cats.index > st) & ((cats.index < pd.Timestamp(en)) if en else True)
            cats.loc[mask] = [set()] * int(mask.sum())
        states[c] = {"close": s, "cats": cats}
    eq, trades, *_ = pf.simulate_alloc(states, dates, 0, 0, same_day=True, held=HELD)
    for t in trades:
        if t["out"] in gone_at.get(t["code"], ()):
            t["why"] = "목록에서 빠짐"
    px = {c: st["close"].reindex(dates).ffill().to_numpy(dtype=float) for c, st in states.items()}
    first_rec = {}
    for rec in sorted(recs, key=lambda r: r["time"]):
        first_rec.setdefault(rec["date"][:4], {it["code"] for it in rec.get("items", [])})
    hold = _hold_nav(px, lambda d: first_rec.get(str(d.year), set(px)), dates)
    lines = {"v1": [float(x) for x in pd.Series(eq).to_numpy()], "hold": hold}
    if idx is None:
        idx = {}
        for k, fc, yc in (("kospi", "KS11", "^KS11"), ("ndx", "IXIC", "^IXIC")):
            try:
                idx[k] = m._index_close(fc, yc)
            except Exception:
                idx[k] = None
    for k in ("kospi", "ndx"):
        s = idx.get(k)
        if s is None or not len(s):
            continue
        s = s.reindex(s.index.union(dates)).ffill().reindex(dates)
        b = s.dropna()
        if len(b):
            lines[k] = [float(v / b.iloc[0]) if pd.notna(v) else None for v in s]
    # 연도별(12/31 결산): 전년 마지막 값(첫해는 시작값) → 그해 마지막 값
    years = []
    for y in sorted({d.year for d in dates}):
        ii = [i for i, d in enumerate(dates) if d.year == y]
        row = {"year": y, "from": f"{dates[ii[0]]:%Y-%m-%d}", "to": f"{dates[ii[-1]]:%Y-%m-%d}", "done": dates[ii[-1]].month == 12 and dates[ii[-1]].day >= 28}
        for k, v in lines.items():
            a = v[ii[0] - 1] if ii[0] > 0 else 1.0
            z = v[ii[-1]]
            row[k] = None if a in (None, 0) or z is None else (z / a - 1) * 100
        years.append(row)
    return {"start": START, "dates": [f"{d:%Y-%m-%d}" for d in dates], "lines": lines, "years": years,
            "trades": [{**t, "name": names.get(t["code"], t["code"]), "in": f"{t['in']:%Y-%m-%d}", "out": f"{t['out']:%Y-%m-%d}"} for t in trades],
            "n_signal_days": len({r["date"] for r in recs})}


# ---------------------------------------------------------------- 신호 추적 (머니파이 LAB 히스토리 anHistTrack 과 같은 방식)
STOP = 8


def track(recs, results):
    """매수(바닥·눌림·추세) → 매도·전량 또는 손절까지 / 매도·전량 → 다음 매수 신호까지. 매수 추적 중 매수 신호가 또 뜨면 같은 추적.
    신호가 = 신호 다음 거래일 종가(국장·미장). 손절 = 신호가 대비 종가 -8% 이하가 다음 날도 이어지면 그날 종가."""
    recs = sorted(recs, key=lambda r: r.get("time", ""))
    per = {}
    for rec in recs:
        for it in rec.get("items", []):
            gs = [history.new_name(x) for x in it.get("groups", [])]
            cat = "sell" if "매도·전량" in gs else "buy" if any(g in BUY_G for g in gs) else "none"
            x = per.setdefault(it["code"], {"name": it["name"], "sig": {}, "last": ""})
            x["name"] = it["name"]
            x["sig"][it["asof"]] = {"cat": cat, "g": next((g for g in gs if g in SELL_G or g in BUY_G), ""), "close": it.get("close")}
            x["last"] = max(x["last"], it["asof"])
    gp = history.gaps(recs)
    closes = {r["code"]: r.get("_c") for r in results if r.get("_c") is not None}
    out = []
    for code, x in per.items():
        sd = sorted(x["sig"])
        c = closes.get(code)
        if c is not None and len(c):
            dates, close = [f"{d:%Y-%m-%d}" for d in c.index], [float(v) for v in c.to_numpy()]
        else:
            dates, close = sd, [float(x["sig"][d]["close"] or 0) for d in sd]
        gone = any(en is None for _, en in gp.get(code, []))            # 지금 목록에서 빠져 있음
        end_date = x["last"] if gone else dates[-1]
        kr = m.is_kr(code)
        ex = lambda i: i + 1                                        # 국장·미장 모두 다음 거래일 종가(2026-10-14)
        first = next((d for d in sd if x["sig"][d]["cat"] != "none"), None)
        if not first:
            continue
        tr = None

        def finish(t, i, why):
            t.update(endI=i, end=dates[i], why=why, ret=(close[i] / t["px"] - 1) * 100)
            out.append(t)

        def start(side, i, s_):
            return {"side": side, "code": code, "name": x["name"], "sig": dates[i], "g": s_["g"], "entI": ex(i), "px": None,
                    "hi": 0.0, "lo": 0.0, "below": False}
        for i in range(len(dates)):
            if dates[i] > end_date:
                break
            if dates[i] < first:
                continue
            if history.in_gap(gp.get(code), dates[i]):                 # 안 본 기간: 추적 중이면 빠지기 전 마지막 날로 끝냄(목록에서 빠짐)
                if tr:
                    li = max(i - 1, 0)
                    tr.update(end=None, endI=li, why="gone", last=dates[li],
                              ret=(close[li] / tr["px"] - 1) * 100 if tr["px"] and li >= tr["entI"] else None)
                    out.append(tr)
                    tr = None
                continue
            s_ = x["sig"].get(dates[i])
            if tr and tr["px"] is None and i == tr["entI"]:
                tr["px"] = close[i]
            if tr and tr["px"] and i > tr["entI"]:
                r = (close[i] / tr["px"] - 1) * 100
                tr["hi"], tr["lo"] = max(tr["hi"], r), min(tr["lo"], r)
                if tr["side"] == "buy":
                    under = r <= -STOP + 1e-9
                    if under and tr["below"]:
                        finish(tr, i, "stop"); tr = None
                    else:
                        tr["below"] = under
            if not s_ or s_["cat"] == "none":
                continue
            if tr and tr["side"] != s_["cat"]:
                j = ex(i)
                if tr["px"] and j < len(dates) and dates[j] <= end_date:
                    for k in range(i + 1, j + 1):
                        r = (close[k] / tr["px"] - 1) * 100
                        tr["hi"], tr["lo"] = max(tr["hi"], r), min(tr["lo"], r)
                    finish(tr, j, "sig")
                else:
                    tr.update(why="wait", endI=None, end=None, ret=(close[i] / tr["px"] - 1) * 100 if tr["px"] else None)
                    out.append(tr)
                tr = None
            if not tr:
                tr = start(s_["cat"], i, s_)
            if tr["px"] is None and i == tr["entI"]:
                tr["px"] = close[i]
        if tr:
            li = len(dates) - 1
            while li > 0 and dates[li] > end_date:
                li -= 1
            tr.update(end=None, endI=li, why="gone" if gone else "open", last=dates[li],
                      ret=(close[li] / tr["px"] - 1) * 100 if tr["px"] and li >= tr["entI"] else None)
            out.append(tr)
    res = []
    for t in out:
        done = t["why"] in ("sig", "stop")
        days = max(t["endI"] - t["entI"], 0) if t.get("endI") is not None and t["px"] else None
        hit = None if not done else (t["why"] != "stop" and t["ret"] > 0) if t["side"] == "buy" else t["ret"] < 0
        res.append({"side": t["side"], "name": t["name"], "code": t["code"], "sig": t["sig"], "g": t["g"],
                    "px": t["px"], "end": t.get("end"), "last": t.get("last"), "why": t["why"], "days": days,
                    "ret": None if t.get("ret") is None else round(t["ret"], 2), "hi": round(t["hi"], 2), "lo": round(t["lo"], 2), "hit": hit})
    return res


def _pct(v):
    return "-" if v is None else f"{v:+.1f}%"


def build(results, out="docs/live.html", recs=None, idx=None):
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    recs = recs if recs is not None else history.load_all()
    res = compute(results, recs, idx)
    try:
        tracks = track(recs, results)
    except Exception as e:
        tracks, track_err = [], f"{type(e).__name__}: {e}"
    else:
        track_err = ""
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    if res:
        with open(os.path.join(os.path.dirname(out) or ".", "live.json"), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
    body = ""
    if not res:
        body = f'<p class="note">{E(START)}부터 기록을 시작합니다. 첫 신호 기록이 쌓이면 여기에 그래프가 그려집니다.</p>'
    else:
        names = dict(LINES)
        keys = [k for k, _ in LINES if k in res["lines"]]
        last = {k: res["lines"][k][-1] for k in keys}
        tot = {k: None if last[k] is None else (last[k] - 1) * 100 for k in keys}
        chips = "".join(f'<button class="c" data-k="{k}"><i style="background:{COLORS[k]}"></i>{E(names[k])} <b>{_pct(tot[k])}</b></button>' for k in keys)
        yh = "".join(f"<th>{E(names[k])}</th>" for k in keys)
        yr = ""
        for y in reversed(res["years"]):
            vals = {k: y.get(k) for k in keys}
            yr += (f'<tr><td class="l"><b>{y["year"]}</b>{"" if y["done"] else " <small>(진행 중)</small>"}<br><small>{y["from"][5:]} ~ {y["to"][5:]}</small></td>'
                   + "".join(f'<td class="{"up" if (vals[k] or 0) > 0 else "dn" if (vals[k] or 0) < 0 else ""}">{_pct(vals[k])}</td>' for k in keys) + "</tr>")
        data = json.dumps({"d": res["dates"], "s": {k: res["lines"][k] for k in keys}, "c": COLORS}, ensure_ascii=False)
        body = f"""<div class="chips" id="chips">{chips}</div>
<div class="card"><svg id="ch" viewBox="0 0 600 260"></svg></div>
<h2>연도별 결산 <small>12/31 기준 · 첫해는 {E(res['start'])}부터</small></h2>
<div class="card"><table><tr><th class="l">연도</th>{yh}</tr>{yr}</table></div>
<script>
const D = {data};
const off = new Set();
function draw() {{
  const W = 600, H = 260, L = 44, R = 8, T = 10, B = 24, n = D.d.length;
  const ks = Object.keys(D.s).filter(k => !off.has(k));
  const all = ks.flatMap(k => D.s[k].filter(v => v != null).map(v => (v - 1) * 100));
  let lo = Math.min(0, ...all), hi = Math.max(1, ...all); const p = (hi - lo) * .06; lo -= p; hi += p;
  const x = i => L + (W - L - R) * (n > 1 ? i / (n - 1) : 0), y = v => T + (H - T - B) * (hi - v) / (hi - lo);
  const st = (hi - lo) > 100 ? 50 : (hi - lo) > 40 ? 20 : (hi - lo) > 15 ? 5 : (hi - lo) > 6 ? 2 : 1;
  let g = '';
  for (let v = Math.ceil(lo / st) * st; v <= hi; v += st) g += `<line x1="${{L}}" x2="${{W - R}}" y1="${{y(v)}}" y2="${{y(v)}}" class="${{v === 0 ? 'z' : ''}}"/><text x="${{L - 5}}" y="${{y(v) + 4}}" text-anchor="end">${{v}}%</text>`;
  [0, Math.floor((n - 1) / 2), n - 1].forEach((i, j) => g += `<text x="${{x(i)}}" y="${{H - 6}}" text-anchor="${{['start', 'middle', 'end'][j]}}">${{D.d[i]}}</text>`);
  ks.forEach(k => {{ let d = '', pen = false; D.s[k].forEach((v, i) => {{ if (v == null) {{ pen = false; return; }} d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y((v - 1) * 100).toFixed(1); pen = true; }});
    g += `<path d="${{d}}" stroke="${{D.c[k]}}" stroke-width="${{k === 'v1' ? 2.4 : 1.6}}" fill="none"/>`; }});
  document.getElementById('ch').innerHTML = g;
}}
document.getElementById('chips').addEventListener('click', e => {{ const b = e.target.closest('button'); if (!b) return;
  off.has(b.dataset.k) ? off.delete(b.dataset.k) : off.add(b.dataset.k); b.classList.toggle('off'); draw(); }});
draw();
</script>"""
    tdata = json.dumps(tracks, ensure_ascii=False)
    body += f"""
<h2 id="trk">신호 추적 <small>신호 하나하나가 맞았나</small></h2>
<div class="flt" id="tflt"><button data-per="3m">3개월</button><button data-per="1y" class="on">1년</button><button data-per="all">전체</button>
  <span class="sep"></span><button data-side="" class="on">전체</button><button data-side="buy">매수</button><button data-side="sell">매도</button></div>
<div id="tsum"></div>
<h3>진행 중 <small id="nopen"></small></h3><div class="card"><table id="topen"></table></div>
<h3>끝난 것 <small id="ndone"></small></h3><div class="card"><table id="tdone"></table></div>
<button class="more" id="tmore">더 보기 (+20)</button>
<p class="note">매수 추적 = 매수 신호부터 매도 신호 또는 손절까지(매수 신호가 또 뜨면 같은 추적) / 매도 추적 = 매도 신호부터 다음 매수 신호까지 /
성공 = 매수는 끝날 때 수익, 매도는 판 뒤 다시 살 때 더 쌈 / 실패 = 신호로 끝났는데 반대 결과(손절은 따로 셈) / 신호가 = 신호대로 샀다면(팔았다면): 신호 다음 거래일 종가 /
손절 = 신호가 대비 종가 -{STOP}% 이하가 다음 날도 이어지면 그날 종가 / 성공률은 끝난 것만 /
위 가상 계좌는 2회 분할로 평단이 섞이고 한도에 걸리면 안 사므로 같은 종목도 수익이 조금 다를 수 있음 / 기존 보유분(신호 전에 산 것)의 손절은 계좌 평단으로 직접 확인{(' / 계산 오류: ' + E(track_err)) if track_err else ''}</p>
<script>
const TR = {tdata};
const TS = {{ per: '1y', side: '', more: 20 }};
const G_BUY = ['매수·바닥', '매수·눌림', '매수·추세'];
const pc = v => v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
const ud = v => v == null ? '' : v > 0 ? 'up' : v < 0 ? 'dn' : '';
const sh = d => d ? d.slice(2).replace(/-/g, '.') : '';
const pr = v => v == null ? '-' : v >= 1000 ? Math.round(v).toLocaleString() : v.toFixed(2);
function judge(t) {{
  if (t.why === 'open' || t.why === 'wait') return '<span class="j op">⏳ 진행중</span>';
  if (t.why === 'gone') return '<span class="j mut">목록에서 빠짐</span>';
  if (t.why === 'stop') return '<span class="j stop">✕ 손절</span>';
  return t.hit ? '<span class="j ok">✓ 성공</span>' : '<span class="j bad">✕ 실패</span>';
}}
function endTxt(t) {{
  if (t.why === 'sig') return sh(t.end) + (t.side === 'buy' ? ' 매도' : ' 매수');
  if (t.why === 'stop') return sh(t.end) + ' 손절';
  if (t.why === 'gone') return sh(t.last) + '까지';
  return t.px == null ? '체결 대기' : '오늘까지';
}}
const row = t => `<tr><td class="l"><b>${{t.name}}</b><br><small>${{sh(t.sig)}}</small></td>
  <td class="l"><span class="g ${{G_BUY.includes(t.g) ? 'b' : 's'}}">${{t.g === '매도·전량' ? '매도' : t.g}}</span><br><small>${{pr(t.px)}}</small></td>
  <td><b class="${{ud(t.ret)}}">${{pc(t.ret)}}</b><br><small><span class="up">${{pc(t.hi)}}</span> / <span class="dn">${{pc(t.lo)}}</span></small></td>
  <td class="l">${{judge(t)}}<br><small>${{endTxt(t)}}${{t.days != null ? ' · ' + t.days + '일' : ''}}</small></td></tr>`;
const head = '<tr><th class="l">종목 · 신호일</th><th class="l">신호 · 신호가</th><th>수익 · 최고/최저</th><th class="l">판정 · 끝난 날</th></tr>';
function rend() {{
  const now = new Date(Date.now() + 9 * 3600e3);
  const ago = mo => {{ const d = new Date(now); d.setUTCMonth(d.getUTCMonth() - mo); return d.toISOString().slice(0, 10); }};
  const from = {{ '3m': ago(3), '1y': ago(12), all: '' }}[TS.per];
  const inPer = TR.filter(t => t.sig >= from);
  const sel = inPer.filter(t => !TS.side || t.side === TS.side);
  const avg = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  let sum = '';
  ['buy', 'sell'].filter(sd => !TS.side || TS.side === sd).forEach(sd => {{
    const t = inPer.filter(x => x.side === sd), d = t.filter(x => x.why === 'sig' || x.why === 'stop'), h = d.filter(x => x.hit);
    const st = d.filter(x => x.why === 'stop').length, op = t.filter(x => x.why === 'open' || x.why === 'wait').length, av = avg(d.map(x => x.ret));
    sum += `<div class="sum ${{sd}}"><b>${{sd === 'buy' ? '매수' : '매도'}} 신호 ${{t.length}}번</b> │ ✓ ${{h.length}}${{d.length ? ' (' + Math.round(h.length / d.length * 100) + '%)' : ''}} │ ✕ ${{d.length - h.length - st}}${{sd === 'buy' ? ' │ 손절 ' + st : ''}} │ ⏳ ${{op}}${{sd === 'buy' ? ` │ 평균 <span class="${{ud(av)}}">${{av != null ? pc(av) : '-'}}</span>` : ''}}</div>`;
  }});
  document.getElementById('tsum').innerHTML = sum;
  const open = sel.filter(t => t.why === 'open' || t.why === 'wait').sort((a, b) => a.sig < b.sig ? 1 : -1);
  const done = sel.filter(t => !(t.why === 'open' || t.why === 'wait')).sort((a, b) => ((a.end || a.last) < (b.end || b.last) ? 1 : -1));
  document.getElementById('nopen').textContent = open.length;
  document.getElementById('ndone').textContent = done.length;
  document.getElementById('topen').innerHTML = head + (open.map(row).join('') || '<tr><td class="l" colspan="4">없음</td></tr>');
  document.getElementById('tdone').innerHTML = head + (done.slice(0, TS.more).map(row).join('') || '<tr><td class="l" colspan="4">아직 없음</td></tr>');
  document.getElementById('tmore').style.display = done.length > TS.more ? '' : 'none';
}}
document.getElementById('tflt').addEventListener('click', e => {{ const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.per != null) {{ TS.per = b.dataset.per; document.querySelectorAll('[data-per]').forEach(x => x.classList.toggle('on', x === b)); }}
  if (b.dataset.side != null) {{ TS.side = b.dataset.side; document.querySelectorAll('[data-side]').forEach(x => x.classList.toggle('on', x === b)); }}
  TS.more = 20; rend(); }});
document.getElementById('tmore').addEventListener('click', () => {{ TS.more += 20; rend(); }});
rend();
</script>"""
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>V1 실전 기록</title><style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --up:#d92d20; --dn:#1d5fd1; --pink:#e0457b; --pinkbg:#fde7ef; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38; --up:#ff6b5e; --dn:#6ea2ff; --pink:#f07aa5; --pinkbg:#3a1a27; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:900px; line-height:1.5; }}
h1 {{ font-size:1.25rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:18px 0 6px; }} h2 small, .t, .note, small {{ color:var(--mut); font-size:.78rem; font-weight:400; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:8px; overflow-x:auto; }}
svg {{ width:100%; height:auto; display:block; }} svg line {{ stroke:var(--line); }} svg line.z {{ stroke:var(--mut); }} svg text {{ fill:var(--mut); font-size:11px; }}
.chips {{ display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 6px; }}
.chips button {{ font:inherit; font-size:.8rem; display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }}
.chips i {{ width:12px; height:3px; border-radius:2px; display:inline-block; }} .chips button.best {{ border-color:var(--pink); background:var(--pinkbg); }} .chips button.off {{ opacity:.35; }}
table {{ border-collapse:collapse; width:100%; font-size:.82rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ color:var(--mut); font-weight:500; }} .l {{ text-align:left; }} td.best {{ background:var(--pinkbg); color:var(--pink); font-weight:700; }}
td.up {{ color:var(--up); }} td.dn {{ color:var(--dn); }} a {{ color:inherit; }}
h3 {{ font-size:.92rem; margin:14px 0 5px; }} h3 small {{ color:var(--mut); font-weight:400; }}
.up {{ color:var(--up); }} .dn {{ color:var(--dn); }} td small {{ color:var(--mut); font-size:.74rem; }} td {{ line-height:1.35; }}
#tdone td, #topen td, #tdone th, #topen th {{ padding:5px 5px; }} #tdone th, #topen th {{ white-space:normal; font-size:.74rem; }}
.flt {{ display:flex; flex-wrap:wrap; gap:5px; margin:6px 0; align-items:center; }} .flt .sep {{ width:1px; height:18px; background:var(--line); margin:0 3px; }}
.flt button, .more {{ font:inherit; font-size:.78rem; padding:3px 11px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--fg); cursor:pointer; }}
.flt button.on {{ background:var(--fg); color:var(--bg); border-color:var(--fg); }} .more {{ display:block; margin:8px auto 0; }}
.sum {{ font-size:.82rem; margin:5px 0; padding:6px 10px; border-radius:8px; background:var(--card); border-left:3px solid var(--pink); }} .sum.sell {{ border-left-color:var(--dn); }}
.g {{ font-size:.72rem; padding:0 5px; border-radius:6px; border:1px solid; font-weight:600; }} .g.b {{ color:var(--pink); }} .g.s {{ color:var(--dn); }}
.j {{ font-size:.74rem; padding:0 5px; border-radius:6px; border:1px solid var(--line); font-weight:700; }} .j.ok {{ color:#0f9d8a; border-color:#0f9d8a; }} .j.bad {{ color:var(--mut); }}
.j.stop {{ color:#c27803; border-color:#c27803; }} .j.op {{ border-style:dashed; }} .j.mut {{ color:var(--mut); border-style:dotted; }}
</style></head><body>
<h1>V1 실전 기록</h1>
<div class="t">{E(START)}부터 매일 리포트의 신호대로 v1 규칙을 따른 가상 계좌 · 갱신 {kst:%Y-%m-%d %H:%M} KST · <a href="./">리포트로</a></div>
<p class="note">백테스트가 아니라 그날 뜬 신호만으로 계산(결과를 미리 아는 효과 없음). 매수: 매수·바닥/눌림/추세 — 신호 다음 거래일 종가 ·
종목당 상한 = 평가액 × min(10%, 150 ÷ 보유 종목 수), 신호 한 번에 상한의 절반(2회 분할) · 매도: 매도 신호 또는 손절 -8%(종가 이틀) → 전량 · 거래비용 포함 · 지수는 배당 제외 ·
'내 종목 그냥 보유' = 시작일과 매년 첫 거래일에 그때 종목을 똑같이 나눠 사서 들고만 있음. 선 이름을 누르면 끄고 켬.</p>
{body}
</body></html>"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    return out
