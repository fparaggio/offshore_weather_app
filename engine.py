"""Weather passage screening, NOT a navigational routing engine."""
from __future__ import annotations
import json, math, os, time, random
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests

BASE=Path(__file__).resolve().parent
DATA=BASE/'data'; DATA.mkdir(exist_ok=True)
# Offshore corridor: >=7 NM from land (Natural Earth 10m) outside the departure gulf and the
# Genova approach; west of Ponza, Montecristo and Pianosa, between Capraia and Cap Corse.
# Unvalidated: verify all legs on nautical charts.
WAYPOINTS=[
 ('Policastro',40.070,15.520),('Golfo di Policastro',39.980,15.460),
 ('Uscita Golfo di Policastro',39.920,15.320),('Largo Palinuro',39.950,15.050),
 ('Tirreno meridionale',40.300,13.900),('Largo Ponza',40.700,12.700),
 ('Tirreno centrale',41.400,11.500),('Largo Montecristo',42.050,10.250),
 ('Ovest Pianosa',42.450,9.860),('Capraia-Capo Corso',43.040,9.610),
 ('Mar Ligure',43.650,9.300),('Avvicinamento Genova',44.150,9.000),
 ('Genova',44.400,8.930)]
MODELS=['ncep_gefs05','ecmwf_ifs025_ensemble']
ATM=['wind_speed_10m','wind_gusts_10m','wind_direction_10m','pressure_msl']
WAVES=['wave_height','wave_period','wave_direction','swell_wave_height','swell_wave_period','swell_wave_direction','wind_wave_height','wind_wave_period','wind_wave_direction']

def nm(a,b):
    la1,lo1,la2,lo2=map(math.radians,(a[0],a[1],b[0],b[1]))
    h=math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2
    return 3440.065*2*math.asin(min(1,math.sqrt(h)))

def route(step_nm=10):
    """Densify a provisional polyline; spherical linear interpolation."""
    pts=[]; dist=0.
    for i in range(len(WAYPOINTS)-1):
        name,lat,lon=WAYPOINTS[i]; nxt=WAYPOINTS[i+1]
        leg=nm((lat,lon),nxt[1:]); n=max(1,math.ceil(leg/step_nm))
        a=np.radians([lat,lon]);b=np.radians(nxt[1:])
        va=np.array([math.cos(a[0])*math.cos(a[1]),math.cos(a[0])*math.sin(a[1]),math.sin(a[0])]);vb=np.array([math.cos(b[0])*math.cos(b[1]),math.cos(b[0])*math.sin(b[1]),math.sin(b[0])])
        theta=math.acos(float(np.clip(va@vb,-1,1)))
        for j in range(n):
            f=j/n
            v=(math.sin((1-f)*theta)*va+math.sin(f*theta)*vb)/math.sin(theta) if theta>1e-10 else va
            la=math.degrees(math.atan2(v[2],math.hypot(v[0],v[1])));lo=math.degrees(math.atan2(v[1],v[0]))
            pts.append({'id':f'P{len(pts):03d}','sector':name,'lat':round(la,5),'lon':round(lo,5),'distance_nm':round(dist+f*leg,2)})
        dist+=leg
    pts.append({'id':f'P{len(pts):03d}','sector':'Genova','lat':WAYPOINTS[-1][1],'lon':WAYPOINTS[-1][2],'distance_nm':round(dist,2)})
    for i,p in enumerate(pts):
        a,b=(p,pts[i+1]) if i+1<len(pts) else (pts[i-1],p)
        p['course_deg']=round(bearing((a['lat'],a['lon']),(b['lat'],b['lon'])),1)
    return pts

def bearing(a,b):
    """Initial great-circle true course from a to b, degrees 0-360."""
    la1,lo1,la2,lo2=map(math.radians,(a[0],a[1],b[0],b[1]))
    y=math.sin(lo2-lo1)*math.cos(la2);x=math.cos(la1)*math.sin(la2)-math.sin(la1)*math.cos(la2)*math.cos(lo2-lo1)
    return math.degrees(math.atan2(y,x))%360

def relative_angle(from_deg,course_deg):
    """Signed angle of a 'coming from' direction vs the bow: 0 ahead, +90 starboard beam, -90 port beam, ±180 astern."""
    if from_deg is None or course_deg is None:return None
    return round((from_deg-course_deg+540)%360-180,1)

def relative_sector(rel):
    if rel is None:return None
    a=abs(rel);side='' if a<=22.5 or a>=157.5 else (' dritta' if rel>0 else ' sinistra')
    kind='prua' if a<=22.5 else 'mascone' if a<=67.5 else 'traverso' if a<=112.5 else 'giardinetto' if a<157.5 else 'poppa'
    return kind+side

def request_json(url,params,cache,refresh=False):
    # Successful responses remain reusable even with --refresh. Refresh is only
    # applied when cache is absent; remove data/raw to force a complete re-fetch.
    if cache.exists():
        try: return json.loads(cache.read_text())
        except (ValueError, OSError): cache.unlink(missing_ok=True)
    for retry in range(4):
        try:
            r=requests.get(url,params=params,timeout=120)
            if r.status_code==429:
                retry_after=r.headers.get('Retry-After','')
                try: delay=min(180,float(retry_after))
                except ValueError: delay=min(180,20*(2**retry))
                print(f'RATE LIMIT {cache.name}: wait {delay:.0f}s (attempt {retry+1}/4)',flush=True)
                if retry==3: return None
                time.sleep(delay+random.uniform(0,3));continue
            r.raise_for_status();j=r.json()
            if isinstance(j,dict) and j.get('error'):raise RuntimeError(j.get('reason','API error'))
            temp=cache.with_suffix('.tmp');temp.write_text(json.dumps(j));temp.replace(cache)
            return j
        except requests.RequestException as e:
            print('REQUEST ERROR',cache.name,str(e)[:220],flush=True)
            if retry==3:return None
            time.sleep(min(60,5*2**retry))
        except Exception as e:
            print('ERROR',cache.name,str(e)[:220],flush=True);return None
    return None

def frame(item):
    if not isinstance(item,dict) or not isinstance(item.get('hourly'),dict): return pd.DataFrame()
    # pandas>=3 infers 's'/'us' resolution; use ns so fractional-second ETAs can be looked up.
    d=pd.DataFrame(item['hourly']);d['time']=pd.to_datetime(d['time'],utc=True).dt.as_unit('ns')
    return d.set_index('time').sort_index()

def members(df,prefix):
    return [c for c in df.columns if c==prefix or c.startswith(prefix+'_member')]

def interpolate(df,ts,col,max_gap_hours=6):
    """Linear time interpolation for scalar variables; no interpolation across missing data."""
    if df.empty or col not in df: return None
    series=pd.to_numeric(df[col],errors='coerce').dropna()
    if series.empty:return None
    idx=series.index.searchsorted(ts)
    if idx<len(series) and series.index[idx]==ts:return float(series.iloc[idx])
    if idx==0 or idx==len(series):return None
    t0,t1=series.index[idx-1],series.index[idx]
    if (t1-t0).total_seconds()>max_gap_hours*3600:return None
    f=(ts-t0)/(t1-t0)
    return float(series.iloc[idx-1]*(1-f)+series.iloc[idx]*f)

def circular_interp(df,ts,col):
    if df.empty or col not in df:return None
    d=df[[col]].dropna();idx=d.index.searchsorted(ts)
    if idx<len(d) and d.index[idx]==ts:return float(d.iloc[idx,0])
    if idx==0 or idx==len(d):return None
    t0,t1=d.index[idx-1],d.index[idx]
    if (t1-t0).total_seconds()>6*3600:return None
    f=(ts-t0)/(t1-t0);a=math.radians(float(d.iloc[idx-1,0]));b=math.radians(float(d.iloc[idx,0]))
    x=(1-f)*math.cos(a)+f*math.cos(b);y=(1-f)*math.sin(a)+f*math.sin(b)
    return round((math.degrees(math.atan2(y,x))%360)%360,1)%360 if math.hypot(x,y)>1e-5 else None

def collect(refresh=False,forecast_days=16,step_nm=10):
    pts=route(step_nm);(DATA/'raw').mkdir(exist_ok=True)
    print('NOTE: cached successful responses are always reused; --refresh fetches only missing batches.',flush=True)
    atm={};sea={};coverage=[]
    # Batches reduce API calls, keep memory manageable.
    for model in MODELS:
        for k in range(0,len(pts),8):
            chunk=pts[k:k+8]
            params={'latitude':','.join(str(p['lat']) for p in chunk),'longitude':','.join(str(p['lon']) for p in chunk),
                    'hourly':','.join(ATM),'models':model,'timezone':'UTC','wind_speed_unit':'kn',
                    'forecast_days':forecast_days,'cell_selection':'sea'}
            j=request_json('https://ensemble-api.open-meteo.com/v1/ensemble',params,DATA/'raw'/f'{model}_{k}.json',refresh)
            items=j if isinstance(j,list) else [j] if j else []
            for p,item in zip(chunk,items):
                df=frame(item);atm[(model,p['id'])]=df
                coverage.append({'type':'atmosphere','model':model,'point':p['id'],'hours':len(df),
                                 'first':str(df.index.min()) if len(df) else None,'last':str(df.index.max()) if len(df) else None,
                                 'members_first':len(members(df,'wind_speed_10m'))})
            time.sleep(1.5)
    for k in range(0,len(pts),8):
        chunk=pts[k:k+8]
        params={'latitude':','.join(str(p['lat']) for p in chunk),'longitude':','.join(str(p['lon']) for p in chunk),
                'hourly':','.join(WAVES),'timezone':'UTC','forecast_days':forecast_days,'cell_selection':'sea'}
        j=request_json('https://marine-api.open-meteo.com/v1/marine',params,DATA/'raw'/f'marine_{k}.json',refresh)
        if j is None:
            params['hourly']='wave_height,wave_period,wave_direction'
            j=request_json('https://marine-api.open-meteo.com/v1/marine',params,DATA/'raw'/f'marine_core_{k}.json',refresh)
        items=j if isinstance(j,list) else [j] if j else []
        for p,item in zip(chunk,items):
            df=frame(item);sea[p['id']]=df
            coverage.append({'type':'marine','model':'marine deterministic','point':p['id'],'hours':len(df),
                             'first':str(df.index.min()) if len(df) else None,'last':str(df.index.max()) if len(df) else None})
        time.sleep(1.5)
    return pts,atm,sea,coverage

def calculate(pts,atm,sea,departures,speeds=(5.,5.5,6.),hour_utc=7,models=None):
    # Values at a route position's ETA for each *coherent* ensemble member.
    samples=[];scenarios=[];comparisons=[]
    for dep in departures:
      for speed in speeds:
       start=pd.Timestamp(f'{dep}T{hour_utc:02d}:00:00Z')
       for model in (MODELS if models is None else models):
        valid_dfs=[atm.get((model,p['id']),pd.DataFrame()) for p in pts]
        nonempty=[d for d in valid_dfs if not d.empty]
        cols=sorted(set.intersection(*(set(members(d,'wind_speed_10m')) for d in nonempty))) if len(nonempty)==len(pts) else []
        if not cols:continue
        for member in cols:
         peak_w=[];peak_g=[];peak_h=[];good_w=0;good_h=0
         for p in pts:
          eta=start+pd.Timedelta(hours=p['distance_nm']/speed)
          a=atm.get((model,p['id']),pd.DataFrame());s=sea.get(p['id'],pd.DataFrame())
          w=interpolate(a,eta,member)
          g=interpolate(a,eta,member.replace('wind_speed_10m','wind_gusts_10m'))
          wd=circular_interp(a,eta,member.replace('wind_speed_10m','wind_direction_10m'))
          pr=interpolate(a,eta,member.replace('wind_speed_10m','pressure_msl'))
          h=interpolate(s,eta,'wave_height');period=interpolate(s,eta,'wave_period')
          direction=circular_interp(s,eta,'wave_direction')
          swell=interpolate(s,eta,'swell_wave_height')
          wave_rel=relative_angle(direction,p['course_deg'])
          row={'departure':dep,'speed_kn':speed,'model':model,'member':member,
               'point_id':p['id'],'sector':p['sector'],'lat':p['lat'],'lon':p['lon'],
               'distance_nm':p['distance_nm'],'course_deg':p['course_deg'],'eta_utc':eta.isoformat(),
               'navigation_day':int((eta-start).total_seconds()//86400)+1,
               'wind_kn':w,'gust_kn':g,'wind_dir_deg':wd,'pressure_hpa':pr,
               'wave_m':h,'wave_period_s':period,'wave_dir_deg':direction,
               'wave_rel_deg':wave_rel,'wave_rel_sector':relative_sector(wave_rel),'swell_m':swell}
          samples.append(row)
          if w is not None:good_w+=1;peak_w.append(w)
          if g is not None:peak_g.append(g)
          if h is not None:good_h+=1;peak_h.append(h)
         scenarios.append({'departure':dep,'speed_kn':speed,'model':model,'member':member,
                           'coverage_wind':good_w/len(pts),'coverage_wave':good_h/len(pts),
                           'max_wind_kn':max(peak_w) if peak_w else None,
                           'max_gust_kn':max(peak_g) if peak_g else None,
                           'max_wave_m':max(peak_h) if peak_h else None})
    df=pd.DataFrame(samples);sc=pd.DataFrame(scenarios)
    if sc.empty:return df,sc,pd.DataFrame()
    for (dep,speed,model),g in sc.groupby(['departure','speed_kn','model']):
        complete=g[g.coverage_wind>=.999]
        wave_complete=g[g.coverage_wave>=.999]
        row={'departure':dep,'speed_kn':speed,'model':model,'n_members':len(g),
             'complete_wind_members':len(complete),'complete_wave_members':len(wave_complete),
             'wind_max_p50_kn':complete.max_wind_kn.median() if len(complete) else None,
             'wind_max_p90_kn':complete.max_wind_kn.quantile(.9) if len(complete) else None,
             'marine_max_wave_m':wave_complete.max_wave_m.median() if len(wave_complete) else None,
             'estimated_hours':pts[-1]['distance_nm']/speed}
        for limit in (20,25,30):
            row[f'fraction_wind_gt_{limit}kn']=float((complete.max_wind_kn>limit).mean()) if len(complete) else None
        comparisons.append(row)
    return df,sc,pd.DataFrame(comparisons)

def run(refresh=False,step_nm=10,speeds=(5.,5.5,6.),departures=None):
    departures=departures or [d.strftime('%Y-%m-%d') for d in pd.date_range('2026-10-17','2026-11-03')]
    pts,atm,sea,coverage=collect(refresh=refresh,step_nm=step_nm)
    samples,scenarios,comparison=calculate(pts,atm,sea,departures,speeds)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    folder=DATA/'runs'/stamp;folder.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(pts).to_csv(folder/'route_points.csv',index=False)
    pd.DataFrame(coverage).to_csv(folder/'coverage.csv',index=False)
    samples.to_csv(folder/'samples.csv',index=False)
    scenarios.to_csv(folder/'scenarios.csv',index=False)
    comparison.to_csv(folder/'comparison.csv',index=False)
    meta={'generated_utc':datetime.now(timezone.utc).isoformat(),'run_id':stamp,'route_nm':pts[-1]['distance_nm'],
          'route_validated':False,'source':'Open-Meteo Ensemble + Marine API','marine_ensemble':False,
          'departure_hour_utc':7,'departures':departures,'speeds_kn':list(speeds),'sampling_step_nm':step_nm,
          'warnings':['PROVISIONAL offshore polyline, NOT chart-validated; possible land crossings or hazards.',
                      'Forecast values at sampled points; no routing optimization or polar performance.',
                      'Marine wave forecasts are not paired with atmospheric ensemble members.',
                      'Missing data are unknown, never zero. Fractions are ensemble frequencies, not calibrated safety probabilities.',
                      'Model initialization and freshness must be checked in upstream metadata.',
                      'Use official bulletins, nautical charts, notices and skipper judgement.']}
    (folder/'meta.json').write_text(json.dumps(meta,indent=2))
    (DATA/'latest.json').write_text(json.dumps({'run_id':stamp}))
    return stamp

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--refresh',action='store_true');p.add_argument('--step-nm',type=float,default=10)
 p.add_argument('--departures',nargs='+',default=None);p.add_argument('--speeds',nargs='+',type=float,default=[5,5.5,6])
 a=p.parse_args();print('Run:',run(a.refresh,a.step_nm,tuple(a.speeds),a.departures))
