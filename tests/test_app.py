import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


@pytest.mark.parametrize(
    'directions,course,expected',
    [
        ([350.,10.],0.,0.),
        ([40.,60.],320.,90.),
        ([220.,240.],320.,-90.),
        ([130.,150.],320.,-180.),
        ([0.,180.],0.,None),
        ([None,None],0.,None),
        ([float('inf'),None],0.,None),
        ([90.,None],0.,90.),
        ([90.,90.],None,None),
    ],
)
def test_timeline_relative_wind(monkeypatch,directions,course,expected):
    samples=pd.DataFrame([
        {
            'departure':'2026-10-30','speed_kn':5.5,'model':'ncep_gefs05',
            'point_id':'P000','eta_utc':'2026-10-30T07:00:00Z',
            'course_deg':course,'wind_dir_deg':direction,
            'wind_kn':10.+i,'gust_kn':15.+i,'pressure_hpa':1010.,
            'wave_m':1.5,'wave_rel_deg':-90.,'wave_rel_sector':'traverso sinistra',
        }
        for i,direction in enumerate(directions)
    ])
    monkeypatch.setattr(app,'read_csv',lambda name:samples)
    rows=app.timeline()
    assert len(rows)==1
    row=rows[0]
    if expected is None:
        assert row['wind_rel_deg'] is None
    else:
        assert row['wind_rel_deg']==pytest.approx(expected)
    assert row['wave_rel_deg']==-90.
    assert row['wave_m']==1.5
    assert row['wind_kn_p50']==10.5
    assert row['n_members']==2


@pytest.mark.parametrize('directions,expected',[([90.,90.],1.),([0.,180.],0.),([0.,90.],2**.5/2),([None,None],None)])
def test_timeline_direction_agreement(monkeypatch,directions,expected):
    samples=pd.DataFrame([{'departure':'2026-10-30','speed_kn':5.5,'model':'ncep_gefs05','point_id':'P000',
        'eta_utc':'2026-10-30T07:00:00Z','course_deg':0.,'wind_dir_deg':d,'wind_kn':10.,'gust_kn':15.,'pressure_hpa':1010.}
        for d in directions])
    monkeypatch.setattr(app,'read_csv',lambda name:samples)
    agreement=app.timeline()[0]['wind_dir_agreement']
    assert agreement is None if expected is None else agreement==pytest.approx(expected,abs=1e-9)


def test_timeline_missing_direction_column(monkeypatch):
    samples=pd.DataFrame([{
        'departure':'2026-10-30','speed_kn':5.5,'model':'ncep_gefs05',
        'point_id':'P000','eta_utc':'2026-10-30T07:00:00Z',
        'wind_kn':10.,'gust_kn':15.,'pressure_hpa':1010.,
    }])
    monkeypatch.setattr(app,'read_csv',lambda name:samples)
    row=app.timeline()[0]
    assert row['wind_dir_deg'] is None
    assert row['wind_rel_deg'] is None
