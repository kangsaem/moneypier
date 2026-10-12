"""무작위 시험 종합(2026-10-12): results/<접두>_<시장>_s<번호>.json 을 모아 한 장으로.
RS_PREFIX = rand20(기본). 판정·차이는 넣은 돈 대비(V1 vs 적립 여섯 경우 평균). 결과: results/<접두>_summary.html
"""
import glob
import json
import os
from statistics import mean, median

PREFIX = os.environ.get("RS_PREFIX", "rand20")


def load():
    out = []
    for f in sorted(glob.glob(f"results/{PREFIX}_*_s*.json")):
        d = json.load(open(f, encoding="utf-8"))
        r = [x["roc"] for x in d["dca"]]
        d["dca_mean"] = mean(r)
        d["dca_mdd"] = mean(x["mdd"] for x in d["dca"])
        d["diff"] = d["v1"]["roc"] - d["dca_mean"]
        d["start"] = d["window"][:10]
        out.append(d)
    return out


def pct(v, signed=True, cls=True):
    if v is None:
        return "-"
    t = f"{v:+.1f}%" if signed else f"{v:.1f}%"
    c = "up" if v > 0 else "dn" if v < 0 else ""
    return f"<span class='{c}'>{t}</span>" if cls and c else t


def block(rows):
    w = sum(r["winner"] == "V1" for r in rows)
    l = sum(r["winner"] == "적립" for r in rows)
    return {"n": len(rows), "w": w, "l": l, "t": len(rows) - w - l,
            "diff_mean": mean(r["diff"] for r in rows), "diff_med": median(r["diff"] for r in rows),
            "v1": mean(r["v1"]["roc"] for r in rows), "dca": mean(r["dca_mean"] for r in rows),
            "v1_mdd": mean(r["v1"]["mdd"] for r in rows), "dca_mdd": mean(r["dca_mdd"] for r in rows),
            "failed": mean(len(r.get("failed", [])) for r in rows)}


def row(nm, b):
    tie = f" (비슷 {b['t']})" if b["t"] else ""
    return (f"<tr><td class='l'>{nm}</td><td>{b['n']}</td><td>{b['w']} : {b['l']}{tie}</td>"
            f"<td>{pct(b['v1'])}</td><td>{pct(b['dca'])}</td><td>{pct(b['diff_mean'])}</td><td>{pct(b['diff_med'])}</td>"
            f"<td>{pct(b['v1_mdd'], cls=False)}</td><td>{pct(b['dca_mdd'], cls=False)}</td><td>{b['failed']:.1f}</td></tr>")


def main():
    data = load()
    if not data:
        print("결과 없음")
        return
    mk_nm = {"kospi": "코스피", "nasdaq": "나스닥"}
    head = ("<tr><th class='l'>구분</th><th>시험</th><th>이긴 횟수 V1 : 적립</th><th>V1 평균</th><th>적립 평균</th>"
            "<th>차이 평균</th><th>차이 중앙값</th><th>최대 하락 V1</th><th>최대 하락 적립</th><th>빠진 후보(평균)</th></tr>")
    h = ["<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
         f"<title>{PREFIX} 종합</title><style>body{{font:13px/1.5 -apple-system,'Noto Sans KR',sans-serif;margin:12px;background:#1E1F22;color:#E8ECF3}}"
         "table{border-collapse:collapse;margin:6px 0 14px}td,th{border-bottom:1px solid #3a3b40;padding:4px 7px;text-align:right;white-space:nowrap}"
         "th{color:#9A9BA1;font-weight:500}.l{text-align:left}.up{color:#FF4D5E}.dn{color:#3D8BFF}.tbl{overflow-x:auto}p,li{color:#9A9BA1}h2{font-size:14px;margin:16px 0 4px}</style>",
         f"<h1 style='font-size:16px'>무작위 시험 종합 · {PREFIX}</h1>",
         "<p>V1(현금 100, 최종 규칙·손절 8%) vs 적립(같은 100을 매달 1·5·10·15·20·25번째 거래일에 나눠 산 여섯 경우의 평균). "
         "기준 = 넣은 돈 대비 수익(둘 다 끝에 넣은 돈 100, 대기 현금 이자 없음). 차이 = V1 − 적립 평균(%p). 판정은 V1이 적립 여섯 경우 범위 밖일 때만 승패.</p>",
         "<h2>시장별</h2><div class='tbl'><table>" + head]
    for mk in ("kospi", "nasdaq"):
        rs = [r for r in data if r["market"] == mk]
        if rs:
            h.append(row(mk_nm[mk], block(rs)))
    h.append(row("전체", block(data)) + "</table></div>")
    # 시작 연도별
    h.append("<h2>시작 연도별</h2><div class='tbl'><table>" + head)
    for mk in ("kospi", "nasdaq"):
        for y in sorted({r["start"][:4] for r in data if r["market"] == mk}):
            rs = [r for r in data if r["market"] == mk and r["start"][:4] == y]
            h.append(row(f"{mk_nm[mk]} {y}년 시작", block(rs)))
    h.append("</table></div>")
    # 전체 목록
    h.append("<h2>시험별</h2><div class='tbl'><table><tr><th class='l'>시장</th><th>번호</th><th class='l'>기간</th><th>종목</th><th>V1</th>"
             "<th>적립(범위)</th><th>차이</th><th class='l'>판정</th><th>최대 하락 V1</th><th>적립</th><th>빠진 후보</th></tr>")
    for r in sorted(data, key=lambda x: (x["market"], x["start"])):
        lo = min(x["roc"] for x in r["dca"])
        hi = max(x["roc"] for x in r["dca"])
        h.append(f"<tr><td class='l'>{mk_nm[r['market']]}</td><td>{r['seed']}</td><td class='l'>{r['window']}</td><td>{r['n']}</td>"
                 f"<td>{pct(r['v1']['roc'])}</td><td>{lo:+.1f}~{hi:+.1f}%</td><td>{pct(r['diff'])}</td><td class='l'>{r['winner']}</td>"
                 f"<td>{r['v1']['mdd']:.1f}%</td><td>{r['dca_mdd']:.1f}%</td><td>{len(r.get('failed', []))}</td></tr>")
    h.append("</table></div>")
    h.append("<p>한계: 상장폐지·합병 등으로 시세를 못 받은 후보는 빠지고 다음 후보로 채움(오래된 기간일수록 생존 편향이 남음). "
             "5년 기간이 최근 10년 안에서 뽑혀 서로 많이 겹치므로 시험끼리 독립이 아님.</p>")
    os.makedirs("results", exist_ok=True)
    with open(f"results/{PREFIX}_summary.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    for mk in ("kospi", "nasdaq"):
        rs = [r for r in data if r["market"] == mk]
        if rs:
            b = block(rs)
            print(mk, f"{b['n']}개 V1 {b['w']} : 적립 {b['l']} · 차이 평균 {b['diff_mean']:+.1f} 중앙값 {b['diff_med']:+.1f} · MDD {b['v1_mdd']:.1f} / {b['dca_mdd']:.1f}")


if __name__ == "__main__":
    main()
