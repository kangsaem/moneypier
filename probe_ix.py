"""indexergo 자료 경로 확인용(2026-10-14) — 페이지·스크립트를 받아 저장"""
import re, requests, os
os.makedirs("results/ix", exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36", "Referer": "https://www.indexergo.com/"}
u = "https://www.indexergo.com/series/?frq=D&idxDetail=20206"
r = requests.get(u, headers=H, timeout=30)
print(r.status_code, len(r.text))
open("results/ix/page.html", "w").write(r.text)
for m in sorted(set(re.findall(r'(?:src|href)="([^"]+\.js[^"]*)"', r.text)))[:30]:
    url = m if m.startswith("http") else "https://www.indexergo.com" + (m if m.startswith("/") else "/series/" + m)
    try:
        t = requests.get(url, headers=H, timeout=30).text
        nm = re.sub(r"[^A-Za-z0-9_.-]", "_", m)[-80:]
        open(f"results/ix/{nm}", "w").write(t)
        print("js", url, len(t))
    except Exception as e:
        print("js 실패", url, e)
print("ajax 후보:", sorted(set(re.findall(r'["\'](/[A-Za-z0-9_/.-]*(?:api|ajax|data|series)[A-Za-z0-9_/.-]*)["\']', r.text)))[:50])
