import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dotenv
import engine
import openai


@pytest.mark.parametrize(
    'code,error_type,message',
    [
        ('credit_balance_exhausted','insufficient_quota','credito o quota'),
        ('insufficient_quota','insufficient_quota','credito o quota'),
        ('rate_limit_exceeded','requests','limite di richieste'),
    ],
)
def test_report_rate_limit_preserves_existing_report(monkeypatch,tmp_path,code,error_type,message):
    folder=tmp_path/'runs'/'test'
    folder.mkdir(parents=True)
    (tmp_path/'latest.json').write_text(json.dumps({'run_id':'test'}))
    (folder/'meta.json').write_text('{}')
    report=folder/'report_ai.md'
    report.write_text('Existing report')
    comparison=pd.DataFrame([{'speed_kn':5.5}])
    coverage=pd.DataFrame([{'type':'atmosphere','model':'test','point':'P000','hours':24}])
    samples=pd.DataFrame([{
        'departure':'2026-10-30','speed_kn':5.5,'model':'test','point_id':'P000',
        'sector':'Test','navigation_day':1,'eta_utc':'2026-10-30T07:00:00Z',
        'wind_kn':10.,'gust_kn':15.,'wave_m':1.,'wave_period_s':5.,
    }])
    comparison.to_csv(folder/'comparison.csv',index=False)
    coverage.to_csv(folder/'coverage.csv',index=False)
    samples.to_csv(folder/'samples.csv',index=False)
    error=openai.RateLimitError(
        'HTTP 429',
        response=Mock(status_code=429),
        body={'code':code,'type':error_type},
    )
    create=Mock(side_effect=error)
    monkeypatch.setattr(engine,'DATA',tmp_path)
    monkeypatch.setattr(dotenv,'load_dotenv',lambda *args,**kwargs:False)
    monkeypatch.setattr(openai,'OpenAI',lambda:SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(sys,'argv',['report.py'])
    with pytest.raises(SystemExit,match=message):
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'report.py'),run_name='__main__')
    create.assert_called_once()
    assert report.read_text()=='Existing report'
    assert (folder/'ai_evidence.json').exists()
