import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import export_static


def make_archive(folder: Path) -> None:
    folder.mkdir()
    (folder / 'meta.json').write_text(json.dumps({'run_id': 'R1', 'departures': ['2026-10-30'], 'route_nm': 10.0}))
    pd.DataFrame([{'id': 'P000', 'lat': 40.0, 'lon': 15.0, 'sector': 'A', 'distance_nm': 0.0}]).to_csv(folder / 'route_points.csv', index=False)
    pd.DataFrame([{'departure': '2026-10-30', 'speed_kn': 6.0, 'model': 'm', 'wind_max_p90_kn': float('nan')}]).to_csv(folder / 'comparison.csv', index=False)
    rows = []
    for speed in (5.5, 6.0):
        for i, member in enumerate(('a', 'b', 'c')):
            rows.append({'departure': '2026-10-30', 'speed_kn': speed, 'model': 'm', 'member': member,
                         'point_id': 'P000', 'sector': 'A', 'lat': 40.0, 'lon': 15.0, 'distance_nm': 0.0,
                         'course_deg': 300.0, 'eta_utc': '2026-10-30T07:00:00+00:00', 'navigation_day': 1,
                         'wind_kn': 10.123 + i, 'gust_kn': 15.0 + i, 'wind_dir_deg': 30.0, 'pressure_hpa': 1010.0,
                         'wave_m': 1.2, 'wave_period_s': 5.0, 'wave_dir_deg': 210.0, 'wave_rel_deg': -90.0,
                         'wave_rel_sector': 'traverso sinistra', 'swell_m': None})
    pd.DataFrame(rows).to_csv(folder / 'samples.csv', index=False)


def test_export_matches_api(tmp_path, monkeypatch):
    archive = tmp_path / 'R1'
    make_archive(archive)
    out = tmp_path / 'docs'
    out.mkdir()
    (out / 'stale.json').write_text('{}')
    info = export_static.export(archive, out)
    assert info['combos'] == 2
    assert not (out / 'stale.json').exists()
    assert (out / '.nojekyll').exists()
    assert '<meta name="static-data" content="data/">' in (out / 'index.html').read_text()
    assert json.loads((out / 'data' / 'comparison.json').read_text())[0]['wind_max_p90_kn'] is None
    combo = json.loads((out / 'data' / 'combo' / '2026-10-30_6_m.json').read_text())
    assert (out / 'data' / 'combo' / '2026-10-30_5.5_m.json').exists()

    monkeypatch.setattr(app, 'latest', lambda: archive)
    expected = app.timeline(departure='2026-10-30', speed=6.0, model='m')
    assert combo['timeline'][0]['wind_kn_p50'] == pytest.approx(expected[0]['wind_kn_p50'], abs=0.01)
    assert combo['timeline'][0]['wind_rel_deg'] == pytest.approx(expected[0]['wind_rel_deg'], abs=0.01)
    assert combo['points']['P000'] == [{'member': 'a', 'wind_kn': 10.12}, {'member': 'b', 'wind_kn': 11.12},
                                       {'member': 'c', 'wind_kn': 12.12}]


def test_speed_key_matches_javascript_number_formatting():
    assert export_static.speed_key(6.0) == '6'
    assert export_static.speed_key(5.5) == '5.5'
