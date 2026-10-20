import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from engine import WAYPOINTS,route,nm,interpolate,circular_interp,calculate,MODELS,bearing,relative_angle,relative_sector

def test_route_monotonic():
    pts=route(10)
    assert len(pts)>30
    assert 350<pts[-1]['distance_nm']<500
    assert all(a['distance_nm']<=b['distance_nm'] for a,b in zip(pts,pts[1:]))

def test_scalar_and_direction_interpolation():
    df=pd.DataFrame({'wind_speed_10m_member01':[10.,20.],'wind_direction_10m_member01':[350.,10.]},index=pd.to_datetime(['2026-10-30T07:00Z','2026-10-30T09:00Z']))
    t=pd.Timestamp('2026-10-30T08:00Z')
    assert interpolate(df,t,'wind_speed_10m_member01')==15
    assert abs(circular_interp(df,t,'wind_direction_10m_member01'))<1

def test_missing_data_not_zero():
    df=pd.DataFrame({'wind_speed_10m_member01':[5.]},index=pd.to_datetime(['2026-10-30T07:00Z']))
    assert interpolate(df,pd.Timestamp('2026-10-31T07:00Z'),'wind_speed_10m_member01') is None

def test_bearing_and_relative_wave_angle():
    assert abs(bearing((40,15),(41,15)))<1e-6
    assert abs(bearing((40,15),(40,16))-90)<1
    assert relative_angle(320,320)==0
    assert relative_angle(50,320)==90
    assert relative_angle(230,320)==-90
    assert abs(relative_angle(140,320))==180
    assert relative_angle(None,320) is None
    assert relative_sector(0)=='prua' and relative_sector(90)=='traverso dritta'
    assert relative_sector(-130)=='giardinetto sinistra' and relative_sector(180)=='poppa'
    assert all(0<=p['course_deg']<360 for p in route(10))

ISLANDS={'Elba':(10.10,10.44,42.71,42.87),'Capraia':(9.78,9.85,43.01,43.07),'Pianosa':(10.05,10.10,42.57,42.61),
 'Montecristo':(10.29,10.32,42.32,42.35),'Giglio':(10.86,10.94,42.32,42.39),'Giannutri':(11.09,11.11,42.24,42.26),
 'Gorgona':(9.89,9.92,43.43,43.45),'Ponza':(12.84,12.99,40.85,40.96),'Ventotene':(13.40,13.44,40.78,40.81)}

def test_route_keeps_offshore_of_islands():
    for p in route(1):
        for name,(x0,x1,y0,y1) in ISLANDS.items():
            q=(min(max(p['lat'],y0),y1),min(max(p['lon'],x0),x1))
            assert nm((p['lat'],p['lon']),q)>=6,(name,p)

def test_route_has_no_sharp_turns():
    for a,b,c in zip(WAYPOINTS,WAYPOINTS[1:],WAYPOINTS[2:]):
        turn=(bearing(b[1:],c[1:])-bearing(a[1:],b[1:])+540)%360-180
        assert abs(turn)<=40,(b[0],turn)
