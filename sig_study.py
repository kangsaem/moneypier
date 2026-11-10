"""sig_dump 결과(results/sigdump_*.csv.gz)로 신호별 거래를 다시 계산하는 연구용 도구(2026-11-11). trades(): 진입 시점·손절·익절·추적·필터 변형. 계좌 제약(현금·상한) 없음 = 신호 하나하나의 결과."""
import pandas as pd, numpy as np, glob
def load():
    fs=sorted(glob.glob('/home/claude/moneypier/results/sigdump_*.csv.gz')); L=[]
    for f in fs:
        d=pd.read_csv(f,dtype={'code':str,'keys':str,'sb':str,'ss':str}); d['uni']=f.split('sigdump_')[1].split('_')[0]; L.append(d)
    d=pd.concat(L); d['keys']=d['keys'].fillna(''); d['date']=pd.to_datetime(d['date'])
    return d
BUY=('buyb','buy','up')
def trades(g, entry='next_close', stop=0.08, stop_days=2, use_sell=True, filt=None, cooldown=5, lim_x=None, tp=None, maxhold=None, trail=None):
    """한 종목: 새 매수 신호마다(보유 중이면 건너뜀) 진입 → 경보(sell) 다음 날 종가 또는 손절로 청산. 수익률 % 목록(비용 0.3% 차감)"""
    g=g.reset_index(drop=True); n=len(g)
    o,h,l,c=(g[k].to_numpy(float) for k in 'ohlc'); keys=g['keys'].tolist()
    typ=[next((k for k in BUY if k in ks.split('|')),None) for ks in keys]
    sell=np.array(['sell' in ks.split('|') for ks in keys])
    out=[]; t=0; last_on=-10**9; free=0
    while t<n-1:
        ty=typ[t]
        if ty is None: t+=1; continue
        new = t-last_on>cooldown; last_on=t
        if not new or t<free or (filt is not None and not filt(g,t,ty)): t+=1; continue
        e=t+1; px=None
        if entry=='same_close': e=t; px=c[t]
        elif entry=='next_open': px=o[e] if o[e]>0 else c[e]
        elif entry=='next_close': px=c[e]
        elif entry=='half': px=((o[e] if o[e]>0 else c[e])+c[e])/2
        elif entry=='limit':      # 다음 날 지정가 = 신호일 종가×(1+lim_x). 저가가 닿으면 체결
            lp=c[t]*(1+(lim_x or 0))
            if o[e]>0 and o[e]<=lp: px=o[e]
            elif l[e]>0 and l[e]<=lp: px=lp
        elif entry=='limit3':
            lp=c[t]*(1+(lim_x or 0))
            for k in range(t+1,min(t+4,n)):
                if o[k]>0 and o[k]<=lp: e=k; px=o[k]; break
                if l[k]>0 and l[k]<=lp: e=k; px=lp; break
        elif entry=='nochase':    # 다음 날 종가가 신호일 종가 +lim_x 이하일 때만 종가 매수
            if c[e]<=c[t]*(1+lim_x): px=c[e]
        elif entry=='limit_else_close':
            lp=c[t]
            if o[e]>0 and o[e]<=lp: px=o[e]
            elif l[e]>0 and l[e]<=lp: px=lp
            else: px=c[e]
        if px is None or not px>0:
            out.append(dict(i=t,date=g.date[t],type=ty,fill=0)); t+=1; continue
        # 청산
        x=None; why='open'; below=0; peak=px
        for k in range(e+1,n):
            peak=max(peak,c[k])
            if stop and c[k]<=px*(1-stop):
                below+=1
                if below>=stop_days: x=k; why='stop'; break
            else: below=0
            if trail and c[k]<=peak*(1-trail): x=k; why='trail'; break
            if tp and c[k]>=px*(1+tp): x=k; why='tp'; break
            if use_sell and sell[k-1] and k-1>e: x=k; why='sell'; break
            if use_sell and sell[k-1] and k-1==e: x=k; why='sell'; break
            if maxhold and k-e>=maxhold: x=k; why='time'; break
        xe=x if x is not None else n-1
        seg=c[e:xe+1]
        out.append(dict(i=t,date=g.date[t],type=ty,fill=1,ei=e,px=px,ret=(c[xe]/px-1)*100-0.3,why=why,hold=xe-e,
                        mae=(min(seg.min(), l[e] if l[e]>0 else px)/px-1)*100,
                        r20=(c[min(e+20,n-1)]/px-1)*100 if e+20<n else np.nan, r60=(c[e+60]/px-1)*100 if e+60<n else np.nan,
                        s20=(c[t+20]/c[t]-1)*100 if t+20<n else np.nan, s60=(c[t+60]/c[t]-1)*100 if t+60<n else np.nan,
                        gap_o=(o[t+1]/c[t]-1)*100 if o[t+1]>0 else np.nan, gap_c=(c[t+1]/c[t]-1)*100,
                        lu=g.lu[t], reg=g.reg[t], rs=g.rs60[t], rsi=g.rsi[t], fh=g.fh[t], nb=g.nb[t], ma=g.ma[t], wa=g.wa[t], bn=g.bn[t]))
        free=xe+1 if x is not None else n
        t+=1
    return out
def run(d, **kw):
    R=[]
    for (u,code),g in d.groupby(['uni','code'],sort=False):
        for r in trades(g.sort_values('date'),**kw): r['uni']=u; r['code']=code; R.append(r)
    return pd.DataFrame(R)
def summ(T):
    T=T[T.fill==1]
    if not len(T): return {}
    w=T.ret>0
    return dict(n=len(T), win=round(w.mean()*100), avg=round(T.ret.mean(),1), med=round(T.ret.median(),1),
                avgW=round(T.ret[w].mean(),1), avgL=round(T.ret[~w].mean(),1), p10=round(T.ret.quantile(.1),1), worst=round(T.ret.min()),
                pf=round(T.ret[w].sum()/-T.ret[~w].sum(),2) if (~w).any() else None, hold=round(T.hold.mean()))
