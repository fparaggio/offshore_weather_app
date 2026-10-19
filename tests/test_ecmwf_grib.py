import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

eccodes = pytest.importorskip('eccodes')
pytest.importorskip('ecmwf.opendata')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ecmwf_grib as grib

RUN = datetime(2026, 10, 19, tzinfo=timezone.utc)
POINTS = [
    {'id': 'P000', 'sector': 'Test', 'lat': 40.1, 'lon': 15.1, 'distance_nm': 0., 'course_deg': 0.},
    {'id': 'P001', 'sector': 'Test', 'lat': 40.2, 'lon': 15.2, 'distance_nm': 6., 'course_deg': 90.},
]


def write_grib(path, stream, step, names=None, missing=False, product='fc', number=None):
    with path.open('wb') as handle:
        for name in names if names is not None else grib.FIELDS[stream]:
            gid = eccodes.codes_grib_new_from_samples('regular_ll_sfc_grib2')
            try:
                for key, value in [
                    ('centre', 'ecmf'), ('setLocalDefinition', 1), ('marsClass', 'od'),
                    ('marsStream', stream), ('marsType', product), ('dataDate', 20261019),
                    ('dataTime', 0), ('step', step), ('shortName', name),
                    ('Ni', 2), ('Nj', 2),
                    ('latitudeOfFirstGridPointInDegrees', 40.25),
                    ('longitudeOfFirstGridPointInDegrees', 15.),
                    ('latitudeOfLastGridPointInDegrees', 40.),
                    ('longitudeOfLastGridPointInDegrees', 15.25),
                    ('iDirectionIncrementInDegrees', .25), ('jDirectionIncrementInDegrees', .25),
                ]:
                    eccodes.codes_set(gid, key, value)
                if number is not None:
                    eccodes.codes_set(gid, 'productDefinitionTemplateNumber', 1)
                    eccodes.codes_set(gid, 'number', number)
                if missing:
                    eccodes.codes_set(gid, 'bitmapPresent', 1)
                    value = eccodes.codes_get(gid, 'missingValue')
                else:
                    value = {'10u': 3., '10v': 4., 'swh': 1.5, 'mwd': 350., 'mwp': 7.}[name]
                eccodes.codes_set_values(gid, [value] * 4)
                eccodes.codes_write(gid, handle)
            finally:
                eccodes.codes_release(gid)


@pytest.mark.parametrize(
    'u,v,direction',
    [(0., -5., 0.), (-5., 0., 90.), (0., 5., 180.), (5., 0., 270.)],
)
def test_wind_convention_and_units(u, v, direction):
    speed, angle = grib.wind_from_uv(u, v)
    assert speed == pytest.approx(5 * 3600 / 1852)
    assert angle == pytest.approx(direction)


def test_calm_and_missing_wind():
    assert grib.wind_from_uv(0., 0.) == (0., None)
    assert grib.wind_from_uv(None, 1.) == (None, None)


@pytest.mark.parametrize('stream', ['oper', 'wave'])
def test_decodes_grib_metadata_and_route_samples(tmp_path, stream):
    path = tmp_path / 'sample.grib2'
    write_grib(path, stream, 360)
    values, metadata = grib.decode_grib(path, RUN, 360, stream, POINTS)
    assert set(values) == set(grib.FIELDS[stream])
    assert all(f['valid_utc'] == '2026-11-03T00:00:00+00:00' for f in metadata)
    assert all(len(v) == 2 for v in values.values())
    assert values[grib.FIELDS[stream][0]][0]['grid_distance_km'] > 0


def test_missing_wave_is_not_zero(tmp_path):
    path = tmp_path / 'missing.grib2'
    write_grib(path, 'wave', 360, missing=True)
    values, _ = grib.decode_grib(path, RUN, 360, 'wave', POINTS)
    assert all(p['value'] is None for v in values.values() for p in v)


def test_perturbed_member_identity_is_validated(tmp_path):
    path = tmp_path / 'member.grib2'
    write_grib(path, 'enfo', 360, names=['10u', '10v'], product='pf', number=7)
    values, fields = grib.decode_grib(
        path, RUN, 360, 'enfo', POINTS, product='pf',
        parameters=('10u', '10v'), member_numbers=(7,),
    )
    assert set(values) == {(7, '10u'), (7, '10v')}
    assert all(f['member'] == 7 for f in fields)
    with pytest.raises(ValueError, match='inatteso'):
        grib.decode_grib(
            path, RUN, 360, 'enfo', POINTS, product='pf',
            parameters=('10u', '10v'), member_numbers=(8,),
        )


def test_multiple_ensemble_members_have_separate_fields(tmp_path):
    first, second, combined = [tmp_path / name for name in ('first.grib2', 'second.grib2', 'both.grib2')]
    write_grib(first, 'enfo', 360, names=['10u', '10v'], product='pf', number=1)
    write_grib(second, 'enfo', 360, names=['10u', '10v'], product='pf', number=2)
    combined.write_bytes(first.read_bytes() + second.read_bytes())
    values, fields = grib.decode_grib(
        combined, RUN, 360, 'enfo', POINTS, product='pf',
        parameters=('10u', '10v'), member_numbers=(1, 2),
    )
    assert len(values) == 4
    assert {f['member'] for f in fields} == {1, 2}


def test_rejects_wrong_run_step_and_incomplete_fields(tmp_path):
    path = tmp_path / 'sample.grib2'
    write_grib(path, 'oper', 360)
    with pytest.raises(ValueError, match='non corrispondenti'):
        grib.decode_grib(path, RUN - timedelta(days=1), 360, 'oper', POINTS)
    with pytest.raises(ValueError, match='non corrispondenti'):
        grib.decode_grib(path, RUN, 354, 'oper', POINTS)
    write_grib(path, 'oper', 360, names=['10u'])
    with pytest.raises(ValueError, match='campi mancanti'):
        grib.decode_grib(path, RUN, 360, 'oper', POINTS)
    write_grib(path, 'oper', 360, names=['10u', '10u'])
    with pytest.raises(ValueError, match='duplicato'):
        grib.decode_grib(path, RUN, 360, 'oper', POINTS)


def test_horizon_is_not_sparse_eta_coverage():
    departure = RUN + timedelta(hours=265)
    check = grib.passage_check(RUN, departure, 6., POINTS, [0, 144, 360])
    assert check['within_model_horizon']
    assert check['eta_points_bracketed_by_downloaded_steps'] == 0
    dense_check = grib.passage_check(RUN, departure, 6., POINTS, [264, 270])
    assert dense_check['eta_points_bracketed_by_downloaded_steps'] == 2
    end = grib.passage_check(RUN, RUN + timedelta(hours=360), 6., POINTS, [354, 360])
    assert not end['within_model_horizon']
    assert end['hours_arrival_beyond_horizon'] == 1.
    past = grib.passage_check(RUN, RUN - timedelta(hours=1), 6., POINTS, [0, 3])
    assert not past['within_model_horizon']


@pytest.mark.parametrize('value', ['2026-10-19T06:00Z', '2026-10-19T12:01Z', '2026-10-19T00:00'])
def test_rejects_unsupported_runs(value):
    with pytest.raises(ValueError):
        grib.parse_run(value)


@pytest.mark.parametrize('speed', [0., -1., float('nan'), float('inf')])
def test_rejects_invalid_speed(speed):
    with pytest.raises(ValueError, match='velocita'):
        grib.passage_check(RUN, RUN, speed, POINTS, [0])


def test_probe_pins_run_reuses_validated_cache_and_preserves_latest(monkeypatch, tmp_path):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def latest(self, **kwargs):
            return RUN.replace(tzinfo=None)

        def retrieve(self, **kwargs):
            assert kwargs['date'] == '20261019'
            assert kwargs['time'] == 0
            assert kwargs['type'] == 'fc'
            calls.append(kwargs)
            write_grib(Path(kwargs['target']), kwargs['stream'], kwargs['step'])

    monkeypatch.setattr(grib, 'Client', Client)
    monkeypatch.setattr(grib, 'DATA', tmp_path)
    monkeypatch.setattr(grib, 'route', lambda step: POINTS)
    latest = tmp_path / 'latest.json'
    latest.write_text('{"run_id":"unchanged"}')
    path = grib.probe(RUN, steps=[0, 360])
    manifest = json.loads(path.read_text())
    assert manifest['ensemble'] is False
    assert manifest['valid_wind_samples'] == 4
    assert manifest['valid_wave_samples'] == 4
    assert len(manifest['files']) == 4
    assert all(len(f['sha256']) == 64 for f in manifest['files'])
    assert len(calls) == 4
    rows = pd.read_csv(path.with_suffix('.csv'))
    assert rows.iloc[0].wind_kn == pytest.approx(5 * grib.KNOTS_PER_MS)
    assert rows.iloc[0].wave_m == 1.5
    grib.probe(RUN, steps=[0, 360], run=RUN)
    assert len(calls) == 4
    assert json.loads(path.read_text())['downloaded_bytes'] == 0
    assert latest.read_text() == '{"run_id":"unchanged"}'
    # An invalid cached product must fail, not silently become valid data.
    write_grib(path.parent / 'oper_000.grib2', 'oper', 3)
    with pytest.raises(ValueError, match='non corrispondenti'):
        grib.probe(RUN, steps=[0, 360], run=RUN)
    assert latest.read_text() == '{"run_id":"unchanged"}'


@pytest.mark.parametrize('steps', [[], [145], [361], list(range(0, 27, 3))])
def test_probe_rejects_oversized_or_invalid_requests(steps):
    with pytest.raises(ValueError, match='Prova limitata'):
        grib.probe(RUN, steps=steps)
