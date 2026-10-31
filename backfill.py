"""빠진 날 채우기(2026-10-31, 10-14 메모 대기 ③ B안)

리포트 실행이 빠지거나 늦어서 어떤 종목의 어떤 거래일(asof) 신호가 기록에 없으면, 그날까지 시세만 잘라
신호를 다시 계산해(백테스트와 같은 방식, 미래 값 안 씀) history/<날짜>_<kr|us>_bf.json 에 "backfill": true 로 남긴다.

- 빠진 날 = 그 종목 일봉의 최근 WINDOW 거래일 중 LIVE_START 이후, 기록(아무 실행이든)에 (종목, asof)가 없는 날.
  국장 실행이 미장 종목의 전날 봉도 기록하므로, 실행 하나가 빠져도 다른 실행이 같은 봉을 기록했으면 빠진 게 아님.
- 대상 종목 = 그날 목록에 있던 종목: 그 날짜 앞뒤 기록에 모두 있고 '목록에서 빠진 기간'이 아님. 지금 목록에 없는 종목은 못 채움.
- 계산 = market_report.analyze_df(그날까지 자른 일봉) + 상대강도 + review. 유효 범위(rng)·시장 지표(market)는 비움.
- 기록 날짜·시각은 정식 실행과 같은 규칙: 국장 종목 = 그날 20:07, 미장 종목 = 다음 날 07:10(KST).
- history.gaps 는 backfill 기록을 '빠짐' 판단에 쓰지 않음(일부 종목만 들어 있으므로).
"""
import json
import os
from datetime import timedelta

import history
import market_report as m

WINDOW = 10
START = os.environ.get("LIVE_START", "2026-10-12")


def _covered(recs):
    have, seen = set(), {}
    for rec in recs:
        for it in rec.get("items", []):
            have.add((it["code"], it.get("asof")))
            seen.setdefault(it["code"], []).append(it.get("asof"))
    return have, {c: sorted(v) for c, v in seen.items()}


def missing(results, recs, window=WINDOW):
    """[(code, 날짜 Timestamp)] — 채울 대상"""
    have, seen = _covered(recs)
    gp = history.gaps(recs)
    out = []
    for r in results:
        df, code = r.get("_df"), r["code"]
        if df is None or code not in seen:
            continue
        c = df["Close"].dropna()
        first, last = seen[code][0], seen[code][-1]
        for d in c.index[-window - 1:-1]:                         # 마지막 봉은 이번 실행이 기록
            ds = f"{d:%Y-%m-%d}"
            if ds < START or ds <= first or ds >= last or (code, ds) in have:
                continue
            if history.in_gap(gp.get(code), ds):
                continue
            out.append((code, d))
    return out


def _item(r, d):
    df = r["_df"]
    cut = df[df.index <= d]
    x = m.analyze_df(cut, r["code"], r["name"], light=True)
    try:
        x["rs60"] = m.rel_strength(cut["Close"].dropna(), m.bench_close(r["code"]))
    except Exception:
        x["rs60"] = None
    x["review"] = m.review(x)
    x["watch"] = r.get("watch")
    return x


def run(results, recs=None, save=True):
    """빠진 (종목, 날짜)를 채워 저장. 저장한 파일 경로 목록"""
    recs = recs if recs is not None else history.load_all()
    todo = missing(results, recs)
    if not todo:
        print("[빠진 날 채우기] 없음")
        return []
    by = {r["code"]: r for r in results}
    groups = {}
    for code, d in todo:
        kr = m.is_kr(code)
        day = d if kr else d + timedelta(days=1)                   # 미장 봉 = 다음 날(KST) 아침 실행이 기록
        while not kr and day.weekday() == 6:                       # 일요일이면 월요일(실제로는 토요일 07:10 실행)
            day += timedelta(days=1)
        key = (f"{day:%Y-%m-%d}", "kr" if kr else "us")
        groups.setdefault(key, []).append((code, d))
    paths = []
    for (date, session), lst in sorted(groups.items()):
        rows = []
        for code, d in lst:
            try:
                rows.append(_item(by[code], d))
            except Exception as e:
                print(f"[빠진 날 채우기] {by[code]['name']} {d:%Y-%m-%d} 실패: {type(e).__name__}: {e}")
        if not rows:
            continue
        rec = history.record(rows, {}, session)
        hm = "20:07" if session == "kr" else "07:10"
        rec.update(date=date, time=f"{date} {hm}", backfill=True)
        for it in rec["items"]:
            it["rng"] = {}
        print(f"[빠진 날 채우기] {date}_{session}: " + ", ".join(f"{x['name']}({x['last']:%m-%d})" for x in rows))
        if save:
            os.makedirs(history.DIR, exist_ok=True)
            path = os.path.join(history.DIR, f"{date}_{session}_bf.json")
            old = None
            if os.path.exists(path):                               # 같은 날 이미 채운 파일이 있으면 합침(같은 종목·봉은 새 값)
                try:
                    with open(path, encoding="utf-8") as f:
                        old = json.load(f)
                except Exception:
                    old = None
            if old and old.get("items"):
                keep = {(it["code"], it["asof"]) for it in rec["items"]}
                rec["items"] = [it for it in old["items"] if (it["code"], it["asof"]) not in keep] + rec["items"]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=0)
            paths.append(path)
        else:
            paths.append(rec)
    return paths
