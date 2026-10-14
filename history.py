"""
신호 기록: 리포트를 만들 때마다 그날 종목별 리포트 칸(매도·전량·매수·바닥·매수·눌림·매수·추세·보유·매수보류·반등대기·하락)을
history/<날짜>_<kr|us>.json 으로 남긴다(daily.yml이 저장소에 커밋).
- 화면: live.py(V1 실전 기록 · 신호 추적, docs/live.html). docs/history.html 은 그쪽으로 이동하는 옛 주소.
- rows_of·month_ret(20·60일 뒤, 기준선)는 옛 화면용 — 지금은 안 씀.
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
                      "rs60": None if r.get("rs60") is None else round(r["rs60"], 1), "groups": groups,
                      # 신호 유효 가격 범위(2026-10-15): {칸: {lo, hi, lo_open, hi_open}} — 다음 거래일 종가가 이 안이면 신호 유지
                      "rng": {k: (None if v is None else {"lo": round(v["lo"], 4), "hi": round(v["hi"], 4),
                                                           "lo_open": v["lo_open"], "hi_open": v["hi_open"]})
                              for k, v in (r.get("vr") or {}).items()}})
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


# ---------------------------------------------------------------- 감시 기간(목록에서 빠졌다 돌아온 구간)
GAP_MIN = 2     # 이 횟수 이상 연속으로 기록에 없어야 '빠짐'으로 봄(시세 실패로 한 번 빠진 것은 무시)


def gaps(recs):
    """종목별 안 본 기간 {code: [(시작, 끝), ...]} — 시작 = 빠지기 전 마지막 기록의 봉 날짜, 끝 = 다시 들어온 첫 기록의 봉 날짜
    (그 사이 날짜가 '안 본 기간'). 지금도 빠져 있으면 끝 = None. 머니파이 anHistGaps 와 같음"""
    last, miss, out = {}, {}, {}
    for rec in sorted(recs, key=lambda r: r.get("time", "")):
        here = {}
        for it in rec.get("items", []):
            here[it["code"]] = it.get("asof")
        for c in last:
            if c not in here:
                miss[c] = miss.get(c, 0) + 1
        for c, asof in here.items():
            if miss.get(c, 0) >= GAP_MIN:
                out.setdefault(c, []).append((last[c], asof))
            miss[c] = 0
            last[c] = asof
    for c, n in miss.items():
        if n >= GAP_MIN:
            out.setdefault(c, []).append((last[c], None))
    return out


def in_gap(gl, d):
    """날짜 d(YYYY-MM-DD)가 안 본 기간 안인가 → 그 구간의 시작(빠지기 전 마지막 봉 날짜) 또는 None"""
    for st, en in gl or []:
        if d > st and (en is None or d < en):
            return st
    return None


# ---------------------------------------------------------------- 계산
def fwd(c, asof, h):
    """asof(그 종목 마지막 봉 날짜) 종가 대비 h거래일 뒤 수익률(%). 아직 안 지났으면 None"""
    if c is None or not len(c):
        return None
    i = c.index.searchsorted(pd.Timestamp(asof), side="right") - 1
    if i < 0 or i + h >= len(c):
        return None
    return float((c.iloc[i + h] / c.iloc[i] - 1) * 100)


OLD_NAME = {"추격매수 주의": "매수보류", "홀딩 유지·순항": "보유·순항", "홀딩 유지·눌림": "보유·조정", "홀딩·순항": "보유·순항", "홀딩·눌림": "보유·조정",
            "대기": "반등대기", "하락-대기": "하락", "하락중": "하락", "상승중": "매수·추세", "매도검토": "매도·전량",
            "매도(전량)": "매도·전량", "매수(바닥)": "매수·바닥", "매수(눌림)": "매수·눌림", "매수(추세)": "매수·추세"}


def new_name(x):
    """2026-10-11 이전 기록의 칸 이름을 지금 이름으로. 매수검토는 근거 수(s) 3개 이상이면 매수·바닥, 아니면 매수·눌림"""
    g = x.get("g", "")
    if g == "매수검토":
        return "매수·바닥" if (x.get("s") or 0) >= 3 else "매수·눌림"
    return OLD_NAME.get(g, g)


def rows_of(recs, closes):
    """기록 → 줄 목록. 같은 종목·같은 asof는 마지막 기록 하나만(국장·미장 리포트가 같은 봉을 두 번 기록하는 경우)"""
    by = {}
    for rec in recs:
        for it in rec.get("items", []):
            by[(it["code"], it["asof"])] = (rec, it)
    rows = []
    for (code, asof), (rec, it) in by.items():
        c = closes.get(code)
        groups = [dict(x, g=new_name(x)) for x in it.get("groups", [])]    # 2026-10-11 이전 기록은 옛 이름
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
GROUP_ORDER = ["매도·전량", "매수·바닥", "매수·눌림", "매수·추세", "보유·순항", "보유·조정", "매수보류", "반등대기", "하락"]
GROUP_WAY = {"매도·전량": -1, "매수·바닥": 1, "매수·눌림": 1, "매수·추세": 1, "보유·순항": 1, "보유·조정": 1, "매수보류": -1, "반등대기": 1, "하락": 1}


def _p(v, d=1):
    return "-" if v is None else f"{v:+.{d}f}%"


def _cls(v, way=1):
    if v is None:
        return "mut"
    return "ok" if v * way > 0 else "bad" if v * way < 0 else ""


def build(results, out="docs/history.html"):
    """2026-10-11부터 신호 기록 화면은 live.html(V1 실전 기록 · 신호 추적)로 합침. 여기선 옛 주소 이동 + 머니파이 가져오기용 history_all.json"""
    recs = load_all()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=live.html#trk">'
                '<title>신호 기록</title></head><body><a href="live.html#trk">V1 실전 기록 · 신호 추적으로 이동 →</a></body></html>')
    with open(os.path.join(os.path.dirname(out), "history_all.json"), "w", encoding="utf-8") as f:   # 머니파이 가져오기용(모든 기록)
        json.dump(recs, f, ensure_ascii=False)
    return out
