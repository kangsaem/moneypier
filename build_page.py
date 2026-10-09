"""
tickers.json(또는 환경변수 TICKERS_JSON)의 종목들로 리포트를 만들어
1) docs/index.html 저장  2) 텔레그램 토큰이 있으면 메시지로도 전송
"""
import contextlib
import html
import io
import json
import os
from datetime import datetime, timedelta, timezone

import requests

import market_report as m


def load_tickers():
    raw = os.environ.get("TICKERS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    with open("tickers.json", encoding="utf-8") as f:
        return json.load(f)


def resolve_codes(items):
    """코드 없이 이름만 있는 종목(금액만 입력한 경우)은 KRX 종목/ETF 목록에서 이름으로 찾는다"""
    if all(t.get("code") for t in items):
        return items
    names = {}
    try:
        import FinanceDataReader as fdr
        for market, col in (("KRX", "Code"), ("ETF/KR", "Symbol")):
            try:
                lst = fdr.StockListing(market)
                names.update(dict(zip(lst["Name"], lst[col])))
            except Exception as e:
                print(f"[{market} 목록] 실패: {e}")
    except Exception as e:
        print(f"[종목 목록] 실패: {e}")
    for t in items:
        if not t.get("code"):
            t["code"] = names.get(t["name"], "")
    return items


tickers = resolve_codes(load_tickers())
unresolved = [t["name"] for t in tickers if not t.get("code")]
tickers = [t for t in tickers if t.get("code")]

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    for t in tickers:
        m.TICKER, m.NAME = t["code"], t["name"]
        m.safe(f"종목 {t['name']}", m.stock_report)
    m.safe("코스피", m.kospi_report)
    m.safe("미국", m.us_report)
report = buf.getvalue()
if unresolved:
    report += "\n[코드를 찾지 못해 제외된 종목] " + ", ".join(unresolved) + "\n"

kst = datetime.now(timezone.utc) + timedelta(hours=9)
stamp = f"{kst:%Y-%m-%d %H:%M} KST"

page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>장 마감 리포트</title>
<style>
:root {{ --bg:#fff; --fg:#1a1a1a; --mut:#666; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111; --fg:#eee; --mut:#999; }} }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; margin:0; padding:16px; }}
h1 {{ font-size:1.2rem; margin:0 0 4px; }}
.t {{ color:var(--mut); font-size:.85rem; margin-bottom:12px; }}
pre {{ white-space:pre-wrap; word-break:break-all; font-size:.85rem; line-height:1.5;
      font-family:ui-monospace,Menlo,Consolas,monospace; }}
</style></head><body>
<h1>장 마감 리포트</h1>
<div class="t">갱신: {stamp} (참고용, 투자 판단 책임은 본인에게 있습니다)</div>
<pre>{html.escape(report)}</pre>
</body></html>"""

os.makedirs("docs", exist_ok=True)
with open("docs/index.html", "w", encoding="utf-8") as f:
    f.write(page)
print("docs/index.html 생성 완료")

# ---- 텔레그램 전송 (선택) ----
token = os.environ.get("TELEGRAM_TOKEN")
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
if token and chat_id:
    text = f"장 마감 리포트 {stamp}\n{report}"
    for i in range(0, len(text), 3800):  # 텔레그램 글자 제한(4096) 대비 분할
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text[i:i + 3800]},
            timeout=15,
        )
        print("텔레그램 전송:", r.status_code)
