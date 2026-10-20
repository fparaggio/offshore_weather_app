from __future__ import annotations
import json, os, html, math
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from engine import DATA,run,relative_angle

app=FastAPI(title='Offshore Weather — Policastro / Genova')
ROOT=Path(__file__).resolve().parent
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')

def latest():
 p=DATA/'latest.json'
 if not p.exists():raise HTTPException(404,'No run available: execute python engine.py first')
 rid=json.loads(p.read_text())['run_id']; folder=DATA/'runs'/rid
 if not folder.exists():raise HTTPException(404,'Run not found')
 return folder

def read_csv(name):
 p=latest()/name
 return pd.read_csv(p,low_memory=False) if p.exists() and p.stat().st_size else pd.DataFrame()

def clean(records):
 def fix(v):
  if isinstance(v,dict):return {k:fix(x) for k,x in v.items()}
  if isinstance(v,list):return [fix(x) for x in v]
  if isinstance(v,np.generic):v=v.item()
  if isinstance(v,float) and not math.isfinite(v):return None
  return v
 return fix(records)

@app.get('/',response_class=HTMLResponse)
def index():
 return (ROOT/'static'/'index.html').read_text()

@app.get('/api/meta')
def meta():
 f=latest();return json.loads((f/'meta.json').read_text())

@app.get('/api/route')
def route():return clean(read_csv('route_points.csv').to_dict('records'))

@app.get('/api/coverage')
def coverage():return clean(read_csv('coverage.csv').to_dict('records'))

@app.get('/api/comparison')
def comparison():return clean(read_csv('comparison.csv').to_dict('records'))

@app.get('/api/timeline')
def timeline(departure:str='2026-10-30',speed:float=5.5,model:str='ncep_gefs05'):
 df=read_csv('samples.csv')
 if df.empty:return []
 return aggregate_timeline(df[(df.departure==departure)&(df.speed_kn==speed)&(df.model==model)])

def aggregate_timeline(df):
 if df.empty:return []
 rows=[]
 for (point,eta),g in df.groupby(['point_id','eta_utc'],sort=False):
  first=g.iloc[0]
  row={k:first.get(k) for k in ['point_id','sector','lat','lon','distance_nm','course_deg','eta_utc','navigation_day']}
  row['n_members']=int(g.wind_kn.count())
  for var in ('wind_kn','gust_kn','pressure_hpa'):
   valid=g[var].dropna()
   for q in (.1,.5,.9):row[f'{var}_p{int(q*100)}']=float(valid.quantile(q)) if len(valid) else None
  directions=g['wind_dir_deg'].dropna().astype(float) if 'wind_dir_deg' in g else pd.Series(dtype=float)
  directions=directions[np.isfinite(directions)]
  row['wind_dir_deg']=None;row['wind_dir_agreement']=None
  if len(directions):
   radians=np.radians(directions)
   x,y=float(np.cos(radians).mean()),float(np.sin(radians).mean())
   row['wind_dir_agreement']=math.hypot(x,y)
   if math.hypot(x,y)>1e-5:row['wind_dir_deg']=math.degrees(math.atan2(y,x))%360
  row['wind_rel_deg']=relative_angle(row['wind_dir_deg'],row['course_deg'])
  for var in ('wave_m','wave_period_s','wave_dir_deg','wave_rel_deg','swell_m'):
   valid=g[var].dropna() if var in g else ();row[var]=float(valid.iloc[0]) if len(valid) else None
  sector=g['wave_rel_sector'].dropna() if 'wave_rel_sector' in g else ()
  row['wave_rel_sector']=sector.iloc[0] if len(sector) else None
  rows.append(row)
 return clean(rows)

@app.get('/api/point/{point_id}')
def point(point_id:str,departure:str='2026-10-30',speed:float=5.5,model:str='ncep_gefs05'):
 df=read_csv('samples.csv')
 if df.empty:return []
 df=df[(df.point_id==point_id)&(df.departure==departure)&(df.speed_kn==speed)&(df.model==model)]
 return clean(df.to_dict('records'))

@app.get('/api/report',response_class=HTMLResponse)
def report():
 return report_html(latest())

def report_html(f):
 md=f/'report_ai.md'
 if not md.exists():return '<p>No AI report yet. Run <code>python report.py</code>.</p>'
 import markdown
 return markdown.markdown(md.read_text(),extensions=['tables','fenced_code'])

class RefreshRequest(BaseModel):
 refresh:bool=True

@app.post('/api/refresh')
def refresh_data(req:RefreshRequest):
 # Intentional: run via CLI to avoid long blocking requests and accidental API bursts.
 return JSONResponse(status_code=409,content={'message':'Execute python update_ecmwf.py for direct ECMWF, or python engine.py --refresh for Open-Meteo, in a separate terminal; then reload this page.'})
