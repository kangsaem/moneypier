"""종목 하나 백테스트(머니파이 lab-bt.html 의 계산 정답지, 2026-10-12)

한 자산(종목 또는 지수)에 V1 규칙을 적용한 계좌. lab-bt.html 의 btSimulate 와 같은 계산이어야 한다.
- 원금 1.0(화면에선 100). 시작 보유 비율 start(0 / 0.5), 그냥 보유 = start 1.0 + 신호 없음
- 매수: 매수 신호(매수·바닥·눌림·추세)가 새로 켜지면(어제 꺼져 있다가 오늘 켜짐, 같은 날 매도 신호 없음) '그해 기준금액 × 1회 비율' 매수.
  그해 기준금액 = 그해 첫 거래일 전날 종가 기준 계좌 평가액(첫해는 원금 1.0), 연중 고정.
  현금이 모자라면 추가금: 연 상한 = 그해 기준금액 × cap(%) (None = 무제한, 0 = 금지). 그래도 모자라면 있는 만큼만,
  살 수 있는 금액이 원래의 25% 미만이면 건너뜀.
- 매도: 매도·전량 신호가 새로 켜지면 전량. 손절: 종가가 평균 매수가 -8% 이하가 이틀 연속이면(산 날 제외) 그날 종가 전량.
- 체결: same_day=True(국내 종목·코스피) = 신호 당일 종가, False(해외 종목·나스닥) = 다음 날 종가.
- 비용: 매수 0.05%, 매도 0.25%(portfolio.py 와 같음). 쉬는 현금 이자 없음.
- 잔고 곡선은 시간가중(추가금 효과 제외). 넣은 돈 대비 수익·연 환산(IRR)은 따로.
"""
import math

BUY_COST = 0.0005
SELL_COST = 0.0025
STOP = 0.92


def irr(flows):
    """flows: [(연 단위 시점, 금액)] → 연 수익률 %(이분법). 해가 없으면 None"""
    def npv(r):
        return sum(a / (1 + r) ** t for t, a in flows)
    lo, hi = -0.99, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2 * 100


def simulate(dates, close, buy_on, sell_on, start=0.0, buy_pct=20.0, cap=None, same_day=True, stop=True):
    """dates: 'YYYY-MM-DD' 목록, close: 종가, buy_on/sell_on: 그날 매수·매도 칸이 켜져 있나(bool 목록).
    stop=False = 손절 없음(그냥 보유 선 — 2026-10-12 버그 수정: 보유 선이 손절돼 현금으로 남던 문제)"""
    n = len(dates)
    cash, sh, entry, cost_sum, warn, last_buy = 1.0, 0.0, 0.0, 0.0, False, -1
    units, inj_year, year, year_base = 1.0, 0.0, dates[0][:4], 1.0
    deposits, trades, nav, cash_ratio, skips = [], [], [], [], 0
    open_tr = None

    def buy(i, amt):
        nonlocal cash, sh, entry, cost_sum, last_buy, open_tr
        p = close[i]
        cash -= amt
        new = amt * (1 - BUY_COST) / p
        entry = (entry * sh + p * new) / (sh + new)
        sh += new
        cost_sum += amt
        last_buy = i
        if open_tr is None:
            open_tr = {"in": dates[i], "inI": i, "buys": 0}
        open_tr["buys"] += 1

    def sell(i, why):
        nonlocal cash, sh, entry, cost_sum, warn, open_tr
        back = sh * close[i] * (1 - SELL_COST)
        cash += back
        trades.append({"in": open_tr["in"], "out": dates[i], "days": i - open_tr["inI"], "buys": open_tr["buys"],
                       "ret": (back / cost_sum - 1) * 100, "why": why})
        sh, entry, cost_sum, warn, open_tr = 0.0, 0.0, 0.0, False, None

    def execute(i, kind):
        nonlocal cash, units, inj_year, skips
        if kind == "sell":
            if sh > 0:
                sell(i, "신호")
            return
        p = close[i]
        eq = cash + sh * p
        want = year_base * buy_pct / 100
        amt = want
        if cash < amt:
            room = math.inf if cap is None else max(year_base * cap / 100 - inj_year, 0.0)
            dep = min(amt - cash, room)
            if dep > 1e-12:
                units += dep / (eq / units)
                cash += dep
                inj_year += dep
                deposits.append((i, dep))
        amt = min(amt, cash)
        if amt < want * 0.25:
            skips += 1
            return
        buy(i, amt)

    if start > 0:
        buy(0, start)
    pending = None
    for i in range(n):
        p = close[i]
        if dates[i][:4] != year:                     # 새해: 전날 종가 기준 평가액으로 기준금액 다시 정함
            year = dates[i][:4]
            year_base = cash + sh * close[i - 1]
            inj_year = 0.0
        if not same_day and pending:
            execute(i, pending)
        pending = None
        if stop and sh > 0 and last_buy != i:        # 손절 -8% 이틀 연속(산 날 제외)
            if p <= entry * STOP:
                if warn:
                    sell(i, "손절")
                else:
                    warn = True
            else:
                warn = False
        if i > 0:
            new_buy = buy_on[i] and not buy_on[i - 1]
            new_sell = sell_on[i] and not sell_on[i - 1]
            if sh > 0 and new_sell:
                pending = "sell"
            elif new_buy and not sell_on[i]:
                pending = "buy"
        if same_day and pending:
            execute(i, pending)
            pending = None
        eq = cash + sh * p
        nav.append(eq / units)
        cash_ratio.append(cash / eq if eq > 0 else 1.0)
    end = cash + sh * close[-1]
    if open_tr is not None:
        trades.append({"in": open_tr["in"], "out": None, "days": n - 1 - open_tr["inI"], "buys": open_tr["buys"],
                       "ret": (sh * close[-1] * (1 - SELL_COST) / cost_sum - 1) * 100, "why": "보유 중"})
    invested = 1.0 + sum(a for _, a in deposits)
    t = lambda i: (_days(dates[0], dates[i])) / 365.25
    flows = [(0.0, -1.0)] + [(t(i), -a) for i, a in deposits] + [(t(n - 1), end)]
    peak, mdd = -math.inf, 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    done = [x for x in trades if x["out"] is not None]
    return {"nav": nav, "trades": trades, "deposits": deposits,
            "twr": (nav[-1] - 1) * 100, "invested": invested * 100, "end": end * 100, "roc": (end / invested - 1) * 100,
            "irr": irr(flows), "mdd": mdd * 100, "n_trades": len(done), "win": (sum(1 for x in done if x["ret"] > 0) / len(done) * 100) if done else None,
            "cash": sum(cash_ratio) / n * 100, "skips": skips}


def simulate_bundle(dates, assets, times=2):
    """묶음 계좌(2026-10-12, lab-bt.html btBundle 과 같은 계산). 종목 n개를 현금 n(화면 100×n) 한 계좌로, 입금 없음.
    dates: 모든 종목 날짜 합집합(정렬). assets: [{"close": [가격 또는 None], "buy": [bool 또는 None], "sell": [...], "same_day": bool}]
      (None = 그 종목이 쉬는 날 — 신호·체결·손절 없음, 평가는 마지막 가격)
    - 종목당 상한 = 계좌 평가액 ÷ n, 신호당 매수 = 상한 ÷ times(1·2·3회). 산다 = min(신호당 매수, 상한 - 그 종목 보유액)
      상한 여유가 신호당 매수의 25% 미만이면 건너뜀, 현금이 살 금액의 50% 미만이면 건너뜀, 그 사이면 있는 현금만큼(portfolio.simulate_alloc 과 같음)
    - 신호 = 그 종목 직전 거래일 대비 새로 켜짐(첫 거래일은 신호 없음). 매도·전량 새로 켜짐 = 전량 매도, 매수 새로 켜짐(같은 날 매도 칸 없음) = 매수
    - same_day = 그날 종가 체결(국내), 아니면 그 종목 다음 거래일 종가(해외). 한 번에 체결하는 주문은 매도 먼저, 그다음 종목 순서
    - 손절: 종가 ≤ 평균 매수가 × 0.92 가 그 종목 거래일 기준 이틀 연속(산 날 제외)이면 그날 종가 전량
    - 하루 순서: 미뤄둔(다음 날) 주문 체결 → 손절 → 오늘 신호 → 당일 체결 주문 실행 → 평가"""
    n_a, n = len(assets), len(dates)
    cash = float(n_a)
    pos, trades, eq_s, cash_ratio = {}, [], [], []
    last_px = [None] * n_a
    prev = [None] * n_a               # 직전 거래일 (buy, sell)
    pending = [None] * n_a            # 다음 거래일에 체결할 주문
    skip_cap = skip_cash = 0

    def value():
        return sum(P["sh"] * last_px[a] for a, P in pos.items())

    def sell(a, i, why):
        nonlocal cash
        P = pos.pop(a)
        p = assets[a]["close"][i]
        back = P["sh"] * p * (1 - SELL_COST)
        cash += back
        trades.append({"a": a, "in": P["in"], "out": dates[i], "days": i - P["inI"], "buys": P["n"], "ret": (back / P["cost"] - 1) * 100, "why": why})

    def run(i, orders):
        nonlocal cash, skip_cap, skip_cash
        for kind, a in sorted(orders, key=lambda o: o[0] != "sell"):
            p = assets[a]["close"][i]
            if kind == "sell":
                if a in pos:
                    sell(a, i, "신호")
                continue
            E = cash + value()
            cap_ = E / n_a
            want = cap_ / times
            have = pos[a]["sh"] * p if a in pos else 0.0
            amt = min(want, cap_ - have)
            if amt < want * 0.25:
                skip_cap += 1
                continue
            if cash < amt * 0.5:
                skip_cash += 1
                continue
            amt = min(amt, cash)
            cash -= amt
            sh = amt * (1 - BUY_COST) / p
            if a in pos:
                P = pos[a]
                P["entry"] = (P["entry"] * P["sh"] + p * sh) / (P["sh"] + sh)
                P["sh"] += sh; P["cost"] += amt; P["n"] += 1
            else:
                pos[a] = {"sh": sh, "cost": amt, "entry": p, "warn": False, "n": 1, "in": dates[i], "inI": i}
            pos[a]["last"] = i

    for i in range(n):
        live = [a for a in range(n_a) if assets[a]["close"][i] is not None]
        for a in live:
            last_px[a] = assets[a]["close"][i]
        due = [(pending[a], a) for a in live if pending[a]]
        for a in live:
            pending[a] = None
        run(i, due)
        for a in live:                                   # 손절
            P = pos.get(a)
            if not P or P["last"] == i:
                continue
            if assets[a]["close"][i] <= P["entry"] * STOP:
                if P["warn"]:
                    sell(a, i, "손절")
                else:
                    P["warn"] = True
            else:
                P["warn"] = False
        today = []
        for a in live:                                   # 오늘 신호
            b, s = bool(assets[a]["buy"][i]), bool(assets[a]["sell"][i])
            pv = prev[a]
            prev[a] = (b, s)
            if pv is None:
                continue
            kind = "sell" if (a in pos and s and not pv[1]) else "buy" if (b and not pv[0] and not s) else None
            if not kind:
                continue
            if assets[a]["same_day"]:
                today.append((kind, a))
            else:
                pending[a] = kind
        run(i, today)
        E = cash + value()
        eq_s.append(E)
        cash_ratio.append(cash / E if E > 0 else 1.0)
    end = eq_s[-1]
    for a, P in pos.items():
        trades.append({"a": a, "in": P["in"], "out": None, "days": n - 1 - P["inI"], "buys": P["n"],
                       "ret": (P["sh"] * last_px[a] * (1 - SELL_COST) / P["cost"] - 1) * 100, "why": "보유 중"})
    peak, mdd = -math.inf, 0.0
    for v in eq_s:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    yrs = _days(dates[0], dates[-1]) / 365.25
    done = [x for x in trades if x["out"] is not None]
    return {"eqs": [v * 100 for v in eq_s], "trades": trades, "invested": 100.0 * n_a, "end": end * 100,
            "roc": (end / n_a - 1) * 100, "irr": ((end / n_a) ** (1 / yrs) - 1) * 100 if yrs > 0 else None, "mdd": mdd * 100,
            "n_trades": len(done), "win": (sum(1 for x in done if x["ret"] > 0) / len(done) * 100) if done else None,
            "n_stop": sum(1 for x in done if x["why"] == "손절"), "cash": sum(cash_ratio) / n * 100,
            "skip_cap": skip_cap, "skip_cash": skip_cash}


def _days(a, b):
    from datetime import date
    da = date(int(a[:4]), int(a[5:7]), int(a[8:10]))
    db = date(int(b[:4]), int(b[5:7]), int(b[8:10]))
    return (db - da).days
