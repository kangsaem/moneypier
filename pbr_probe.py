"""코스피 PBR 공개 출처 확인(2026-10-16, 로그인·계정 없이 받을 수 있는지). 결과: results/pbr_probe.txt
이미 막힌 것(2026-10-10·14): KRX 정보데이터시스템(pykrx) = 로그인 요구, indexergo = 403.
이번에 다시 보는 것: ① KRX 지수 PER/PBR 화면 요청(브라우저 머리글) ② indexergo(브라우저 머리글) ③ 네이버 코스피 지수 응답 안 PER·PBR 항목"""
import json
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"


def show(name, r, n=400):
    body = r.text if hasattr(r, "text") else str(r)
    print(f"--- {name}: HTTP {getattr(r, 'status_code', '?')} · {len(body)}자")
    print(body[:n].replace("\n", " "))


def tryit(name, fn):
    try:
        show(name, fn())
    except Exception as e:
        print(f"--- {name}: 실패 {type(e).__name__} {e}")


# ① KRX: 지수 PER/PBR/배당수익률(코스피 1001), 최근 한 달
krx_h = {"User-Agent": UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010107",
         "X-Requested-With": "XMLHttpRequest"}
for bld in ("dbms/MDC/STAT/standard/MDCSTAT00702", "dbms/MDC/STAT/standard/MDCSTAT00701"):
    tryit("KRX " + bld, lambda bld=bld: requests.post("http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", headers=krx_h, timeout=20,
          data={"bld": bld, "indIdx": "1", "indIdx2": "001", "tboxindIdx_finder_equidx0_0": "코스피", "codeNmindIdx_finder_equidx0_0": "코스피",
                "strtDd": "20260901", "endDd": "20261015", "searchType": "P", "csvxls_isNo": "false"}))

# ② indexergo: 화면 주소와 같은 사이트의 응답
ig_h = {"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8", "Accept-Language": "ko-KR,ko;q=0.9"}
tryit("indexergo 화면", lambda: requests.get("https://www.indexergo.com/series/?frq=D&idxDetail=20206", headers=ig_h, timeout=20))

# ③ 네이버: 코스피 지수 응답에 PER·PBR 항목이 있는지(현재 값만이라도)
nv_h = {"User-Agent": UA, "Referer": "https://m.stock.naver.com/"}
for u in ("https://m.stock.naver.com/api/index/KOSPI/integration", "https://m.stock.naver.com/api/index/KOSPI/basic",
          "https://m.stock.naver.com/api/index/KOSPI/price?pageSize=5&page=1"):
    try:
        r = requests.get(u, headers=nv_h, timeout=20)
        t = r.text
        keys = sorted({k for k in json.dumps(r.json(), ensure_ascii=False).replace('"', ' ').split() if any(x in k.lower() for x in ("per", "pbr", "배수"))}) if r.ok else []
        print(f"--- 네이버 {u}: HTTP {r.status_code} · PER/PBR 관련 글자: {keys[:20]}")
        i = t.lower().find("pbr")
        print("   pbr 주변:", t[max(0, i - 150): i + 150].replace("\n", " ") if i >= 0 else "없음")
    except Exception as e:
        print(f"--- 네이버 {u}: 실패 {e}")
