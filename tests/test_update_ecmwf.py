import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip('eccodes')
pytest.importorskip('ecmwf.opendata')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import update_ecmwf as updater
READ_PRODUCT = updater.read_product

RUN = datetime(2026, 10, 19, tzinfo=timezone.utc)
POINTS = [
    {'id': 'P000', 'sector': 'Test', 'lat': 40., 'lon': 15., 'distance_nm': 0., 'course_deg': 0.},
    {'id': 'P001', 'sector': 'Test', 'lat': 40.1, 'lon': 15., 'distance_nm': 6., 'course_deg': 90.},
]


def values(value):
    return [{'value': value, 'grid_lat': p['lat'], 'grid_lon': p['lon'], 'grid_distance_km': 0.} for p in POINTS]


def fake_product(client, folder, run, step, stream, product, numbers, points, budget):
    if stream == 'wave':
        data = {'swh': values(1.5), 'mwd': values(350.), 'mwp': values(7.)}
        data['swh'][1]['value'] = None
    else:
        data = {'10u': values(3.), '10v': values(4.), 'msl': values(101000.), '10fg': values(8.)}
        if product == 'pf':
            data = {(n, field): value for n in numbers for field, value in data.items()}
    return data, {'file': f'{stream}_{product}_{step}', 'bytes': 0, 'fields': []}


@pytest.fixture
def environment(monkeypatch, tmp_path):
    class Client:
        def __init__(self, **kwargs):
            pass

        def latest(self, **kwargs):
            return RUN.replace(tzinfo=None)

    monkeypatch.setattr(updater, 'DATA', tmp_path)
    monkeypatch.setattr(updater, 'route', lambda step: POINTS)
    monkeypatch.setattr(updater, 'Client', Client)
    monkeypatch.setattr(updater, 'read_product', fake_product)
    (tmp_path / 'latest.json').write_text('{"run_id":"previous"}')
    return tmp_path


def test_native_steps_bracket_all_requested_etas():
    steps = updater.required_steps(RUN, POINTS, ['2026-10-30'], (6.,))
    assert steps == [270, 276]
    assert updater.required_steps(RUN, POINTS, ['2026-10-19'], (6.,)) == [6, 9]
    with pytest.raises(ValueError, match='Nessuna ETA'):
        updater.required_steps(RUN, POINTS, ['2026-11-10'], (6.,))


def test_publishes_full_ensemble_and_api_can_read_it(environment, monkeypatch):
    path = updater.update(['2026-10-30'], speeds=(6.,))
    latest = json.loads((environment / 'latest.json').read_text())
    assert latest['run_id'] == path.name
    meta = json.loads((path / 'meta.json').read_text())
    assert meta['full_ensemble']
    assert len(meta['ensemble_members']) == 51
    assert meta['marine_ensemble'] is False
    assert meta['passage_checks'][0]['within_model_horizon']
    assert meta['passage_checks'][0]['eta_points_bracketed_by_downloaded_steps'] == 2
    samples = pd.read_csv(path / 'samples.csv')
    assert len(samples) == 102
    assert samples.member.nunique() == 51
    assert samples.wind_kn.notna().all()
    assert samples.pressure_hpa.iloc[0] == 1010.
    assert samples.gust_kn.iloc[0] == pytest.approx(8 * updater.KNOTS_PER_MS)
    assert samples.loc[samples.point_id == 'P001', 'wave_m'].isna().all()
    comparison = pd.read_csv(path / 'comparison.csv')
    assert comparison.complete_wind_members.iloc[0] == 51
    assert pd.isna(comparison.marine_max_wave_m.iloc[0])
    assert comparison.fraction_wind_gt_25kn.iloc[0] == 0.
    monkeypatch.setattr(app, 'DATA', environment)
    timeline = app.timeline(departure='2026-10-30', speed=6., model=updater.MODEL)
    assert len(timeline) == 2
    assert timeline[0]['n_members'] == 51
    assert timeline[0]['wave_m'] == 1.5
    assert timeline[1]['wave_m'] is None
    assert timeline[0]['wind_kn_p50'] == pytest.approx(5 * updater.KNOTS_PER_MS)
    assert timeline[0]['wind_rel_deg'] == -143.1


def test_subset_is_labelled_and_no_publish_preserves_active_run(environment):
    path = updater.update(['2026-10-30'], speeds=(6.,), numbers=(0, 2), publish_latest=False)
    meta = json.loads((path / 'meta.json').read_text())
    assert not meta['full_ensemble']
    assert '2/51' in meta['models'][0]['label']
    assert json.loads((environment / 'latest.json').read_text())['run_id'] == 'previous'


def test_plan_does_not_publish_or_download(environment, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Plan must not download')
    monkeypatch.setattr(updater, 'read_product', forbidden)
    assert updater.update(['2026-10-30'], plan=True) is None
    assert not (environment / 'runs').exists()
    assert json.loads((environment / 'latest.json').read_text())['run_id'] == 'previous'


def test_download_failure_never_changes_latest(environment, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Network unavailable')
    monkeypatch.setattr(updater, 'read_product', fail)
    with pytest.raises(OSError, match='Network'):
        updater.update(['2026-10-30'])
    assert json.loads((environment / 'latest.json').read_text())['run_id'] == 'previous'
    assert not (environment / 'runs').exists()


def test_publication_failure_never_changes_latest(environment, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('Disk full')
    monkeypatch.setattr(pd.DataFrame, 'to_csv', fail)
    with pytest.raises(OSError, match='Disk full'):
        updater.update(['2026-10-30'])
    assert json.loads((environment / 'latest.json').read_text())['run_id'] == 'previous'


def test_partial_horizon_stays_missing_and_comparison_unknown(environment, monkeypatch):
    points = [dict(POINTS[0]), dict(POINTS[1], distance_nm=60.)]
    monkeypatch.setattr(updater, 'route', lambda step: points)
    path = updater.update(['2026-11-03'], speeds=(6.,), run=RUN + timedelta(hours=12),
                          numbers=(0, 1))
    meta = json.loads((path / 'meta.json').read_text())
    assert meta['steps_hours'] == [354, 360]
    assert not meta['passage_checks'][0]['within_model_horizon']
    samples = pd.read_csv(path / 'samples.csv')
    assert samples.loc[samples.point_id == 'P000', 'wind_kn'].notna().all()
    assert samples.loc[samples.point_id == 'P001', 'wind_kn'].isna().all()
    comparison = pd.read_csv(path / 'comparison.csv')
    assert comparison.complete_wind_members.iloc[0] == 0
    assert pd.isna(comparison.wind_max_p90_kn.iloc[0])
    assert pd.isna(comparison.fraction_wind_gt_25kn.iloc[0])


def test_product_budget_stops_before_network(environment, monkeypatch):
    from types import SimpleNamespace
    def forbidden(**kwargs):
        pytest.fail('Budget must stop before download')
    with pytest.raises(ValueError, match='Budget'):
        READ_PRODUCT(
            SimpleNamespace(retrieve=forbidden), environment, RUN, 3, 'enfo', 'pf',
            (1, 2), POINTS, {'bytes': 0, 'limit': 1},
        )
    assert not list(environment.glob('*.part'))


@pytest.mark.parametrize('kwargs', [
    {'speeds': (0.,)}, {'speeds': (float('nan'),)}, {'numbers': ()},
    {'numbers': (1, 1)}, {'numbers': (51,)}, {'step_nm': 0.},
    {'max_download_mb': -1.},
])
def test_invalid_input_does_not_publish(environment, kwargs):
    with pytest.raises(ValueError):
        updater.update(['2026-10-30'], **kwargs)
    assert json.loads((environment / 'latest.json').read_text())['run_id'] == 'previous'
