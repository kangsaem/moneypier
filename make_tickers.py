"""
머니파이에서 내보낸 엑셀(money-pie-날짜.xlsx)에서 종목 목록을 뽑아 tickers.json 생성
사용: python make_tickers.py money-pie-2026-10-09.xlsx
설치: pip install pandas openpyxl
"""
import json
import sys

import pandas as pd

path = sys.argv[1]
df = pd.read_excel(path, sheet_name="보유종목", dtype=str).fillna("")

seen, out = set(), []
for _, r in df.iterrows():
    name, region = r["종목"], r["지역"]
    symbol, yahoo = r["심볼"].strip(), r["야후심볼"].strip()
    if name in ("원화", "외화"):          # 현금 항목 제외
        continue
    # 국내: 네이버 6자리 코드 / 해외: 야후 심볼 우선
    code = symbol if (region == "국내" and symbol) else (yahoo or symbol)
    if not code or code in seen:
        continue
    seen.add(code)
    out.append({"code": code, "name": name})

with open("tickers.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"{len(out)}개 종목 저장 → tickers.json")
for o in out:
    print(" ", o["code"], o["name"])
