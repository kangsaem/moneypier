"""
백테스트·추적 실행 기록 목록: results/*.json(실행마다 남는 요약)을 모아 results/backtests.html을 만든다.
같은 대상(코스피·나스닥·내 종목·종목 추적)끼리 최신순으로 나란히 놓아 로직을 바꿀 때마다 결과가 어떻게 달라졌는지 비교.
사용: python bt_index.py   (backtest_wide.yml·bottom_trace.yml이 결과 저장 뒤에 실행)
"""
import glob
import html
import json
import os
from datetime import datetime, timedelta, timezone

DIR = os.environ.get("BT_OUT_DIR", "results")
# 비교표에 보여 줄 분류와 기대 방향(▲ 오르면 좋음 / ▼ 덜 오르면 좋음)
PORT_COLS = ("v1+상승중 전부", "상한8%·5%", "상한5%·2.5%", "상한2%·1%", "상한8%·5% 당일종가", "상한8%·5% 깨지면 안 삼")
PORT_PREFIX = ("N100 ", "N150 ", "N200 ")   # 종목 수 기준 상한(입금 없음)은 이름이 대상마다 달라 앞부분으로 찾음
KEY_CATS = [("v1·매수검토", 1), ("v1·매수검토●2", 1), ("v1·매수검토●3", 1), ("v1·매도검토", -1), ("v1·매도검토●2", -1), ("v1·매수보류", -1), ("v1·홀딩", 1), ("매수", 1), ("눌림진행", 1), ("불타기", 1), ("골든X3", 1), ("바닥1/3", 1), ("바닥3/3", 1),
            ("매도", -1), ("익절검토", -1), ("과열", -1), ("꼭지1/3", -1), ("꼭지3/3", -1),
            ("매도·상대강도↓", -1), ("비중축소·상대강도↓", -1)]
E = html.escape


def load():
    seen, out = set(), []
    for f in sorted(glob.glob(os.path.join(DIR, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        key = (d.get("kind"), d.get("label"), d.get("version"), d.get("time"))
        if key in seen:                       # 최신본 복사(같은 실행)는 한 번만
            continue
        seen.add(key)
        out.append(d)
    return out


def cell(v, way):
    if v is None:
        return '<td class="mut">-</td>'
    good = v * way > 0
    cls = "ok" if good and abs(v) >= 1 else "bad" if (not good) and abs(v) >= 1 else ""
    return f'<td class="{cls}">{v:+.1f}</td>'


def pct(v):
    return "-" if v is None else f"{v:+.0f}%"


def bt_table(items):
    head = "".join(f'<th>{E(c)} <small>{"▲" if w > 0 else "▼"}</small></th>' for c, w in KEY_CATS)
    rows = ""
    for d in items:
        cats = d.get("cats", {})
        cells = "".join(cell((cats.get(c, {}).get("d") or {}).get("20"), w) for c, w in KEY_CATS)
        port = {p["name"]: p for p in d.get("port", [])}
        rep = port.get("리포트 + 손절8%", {}) or port.get("리포트 규칙", {})
        cols = [port.get(n, {}) for n in PORT_COLS] + [next((p for nm, p in port.items() if nm.startswith(pf) and nm.endswith("·2회")), {}) for pf in PORT_PREFIX]
        bm = next((p for n, p in port.items() if n.endswith("보유") and n != "균등 보유"), {})
        f = d.get("files", {})
        rows += (f'<tr><td class="c"><b>{E(d.get("time", ""))}</b><br><small>{E(d.get("version", ""))} · {E(d.get("commit", "") or "-")}</small></td>'
                 f'<td class="c memo">{E(d.get("memo", "") or "-")}</td>'
                 f'<td class="c"><a href="{E(f.get("html", ""))}">결과</a> · <a href="{E(f.get("portfolio", ""))}">계좌</a><br>'
                 f'<small>{E(d.get("period", ""))} · {d.get("stocks", "")}종목</small></td>'
                 f'{cells}<td class="g">{pct(rep.get("tot"))}</td>{"".join(f"<td>{pct(c.get('tot'))}</td>" for c in cols)}'
                 f'<td>{pct(port.get("균등 보유", {}).get("tot"))}</td><td>{pct(bm.get("tot"))}</td></tr>')
    return (f'<div class="wrap"><table><tr><th class="c">실행</th><th class="c">메모(바꾼 로직)</th><th class="c">파일</th>{head}'
            f'<th class="g">계좌 · 구버전+손절8%</th>{"".join(f"<th>{E(n)}</th>" for n in PORT_COLS)}{"".join(f"<th>{E(pf.strip())}÷N·2회</th>" for pf in PORT_PREFIX)}<th>균등 보유</th><th>지수 보유</th></tr>{rows}</table></div>')


def tr_table(items):
    rows = ""
    for d in items:
        def eps(key, w):
            parts = []
            for e in d.get(key, [])[-3:]:
                st = e.get("st", {})
                s = " · ".join(f'{k}/3 {v["date"][5:]}({w} {v["vs"]:+.0f}%)' for k, v in sorted(st.items()))
                parts.append(f'{w} {e["ext_date"][2:]} → {s}')
            return "<br>".join(E(p) for p in parts) or "-"
        rows += (f'<tr><td class="c"><b>{E(d.get("time", ""))}</b><br><small>{E(d.get("version", ""))} · {E(d.get("commit", "") or "-")}</small></td>'
                 f'<td class="c memo">{E(d.get("memo", "") or "-")}</td><td class="c"><a href="{E(d["files"]["html"])}">결과</a></td>'
                 f'<td class="c">{eps("bottom", "저점")}</td><td class="c">{eps("top", "고점")}</td></tr>')
    return (f'<div class="wrap"><table><tr><th class="c">실행</th><th class="c">메모</th><th class="c">파일</th>'
            f'<th class="c">바닥 단계 (최근 3개 저점)</th><th class="c">꼭지 단계 (최근 3개 고점)</th></tr>{rows}</table></div>')


def main():
    data = load()
    groups = {}
    for d in data:
        groups.setdefault((d.get("kind"), d.get("label")), []).append(d)
    body = ""
    for (kind, label), items in sorted(groups.items(), key=lambda kv: (kv[0][0] != "backtest", kv[0][1] or "")):
        items.sort(key=lambda d: d.get("time", ""), reverse=True)
        body += f'<h2>{E(label or "")} <small>{len(items)}회</small></h2>' + (bt_table(items) if kind == "backtest" else tr_table(items))
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>백테스트 기록</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --fg:#14181f; --mut:#6b7380; --line:#e3e6eb; --ok:#0f766e; --okbg:#d9f2ee; --bad:#d92d20; --badbg:#fdecea; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --fg:#eceff4; --mut:#9aa3b2; --line:#2a2f38; --ok:#4fd1c0; --okbg:#10302c; --bad:#ff6b5e; --badbg:#3a1d1a; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,"Noto Sans KR",sans-serif; margin:0 auto; padding:14px; max-width:1200px; line-height:1.5; }}
h1 {{ font-size:1.3rem; margin:4px 0; }} h2 {{ font-size:1rem; margin:20px 0 6px; }} .t, .note {{ color:var(--mut); font-size:.8rem; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:12px; }}
table {{ border-collapse:collapse; width:100%; font-size:.8rem; }} th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; vertical-align:top; }}
th {{ color:var(--mut); font-weight:500; }} td.c, th.c {{ text-align:left; }} td.memo {{ white-space:normal; min-width:160px; max-width:260px; }}
small {{ color:var(--mut); }} td.ok {{ background:var(--okbg); color:var(--ok); font-weight:600; }} td.bad {{ background:var(--badbg); color:var(--bad); }}
td.mut {{ color:var(--mut); }} .g {{ border-left:1px solid var(--line); }} a {{ color:inherit; }}
</style></head><body>
<h1>백테스트 기록</h1>
<div class="t">실행할 때마다 결과가 버전별로 쌓임 · 같은 대상끼리 최신순 · 갱신 {kst:%Y-%m-%d %H:%M} KST · <a href="./">리포트로</a></div>
<p class="note">분류 칸 = 신호 뒤 20거래일 평균 수익률 − 기준선(아무 날이나 샀을 때) (%p). ▲ 분류는 +, ▼ 분류는 −가 기대 방향 —
기대 방향으로 1%p 이상이면 초록, 반대로 1%p 이상이면 빨강. 5건 미만은 '-'. 계좌 = 방식별 총수익(구버전+손절8% / v1 = 매수검토에 사고 매도검토·손절8%에 팔기 / +상승중 = 상승중에도 매수 / 상한a%·b% = 신호마다 계좌의 b% 매수·종목당 a% 상한)과 같은 종목 균등 보유·지수 보유 총수익.</p>
{body or '<p class="note">아직 버전으로 저장된 실행이 없음 — backtest-wide 또는 bottom-trace를 한 번 돌리면 생김</p>'}
</body></html>"""
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, "backtests.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"{DIR}/backtests.html 생성 완료 (실행 {len(data)}개)")


if __name__ == "__main__":
    main()
