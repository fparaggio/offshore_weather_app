"""Bounded ECMWF Open Data trial, isolated from the dashboard forecast archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import eccodes
import pandas as pd
from ecmwf.opendata import Client

from engine import DATA, relative_angle, route

FIELDS = {'oper': ('10u', '10v'), 'wave': ('swh', 'mwd', 'mwp')}
UNITS = {'10u': 'm s**-1', '10v': 'm s**-1', '10fg': 'm s**-1',
         '10fg3': 'm s**-1', '10fg6': 'm s**-1', 'msl': 'Pa',
         'swh': 'm', 'mwd': 'Degree true', 'mwp': 's'}
STEPS = tuple(range(0, 145, 3)) + tuple(range(150, 361, 6))
KNOTS_PER_MS = 3600 / 1852


def parse_run(value: str) -> datetime:
    run = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if run.tzinfo is None:
        raise ValueError('Il run deve includere il fuso UTC, ad esempio 2026-10-19T00:00Z')
    run = run.astimezone(timezone.utc)
    if run.hour not in (0, 12) or run.minute or run.second or run.microsecond:
        raise ValueError('Per 360 ore occorre un run delle 00 o 12 UTC')
    return run


def wind_from_uv(u: float | None, v: float | None) -> tuple[float | None, float | None]:
    if u is None or v is None:
        return None, None
    speed = math.hypot(u, v)
    direction = math.degrees(math.atan2(-u, -v)) % 360 if speed > 1e-5 else None
    return speed * KNOTS_PER_MS, direction


def decode_grib(
    path: Path, run: datetime, step: int, stream: str, points: list[dict],
    product: str = 'fc', parameters: tuple[str, ...] | None = None,
    member_numbers: tuple[int, ...] | None = None,
) -> tuple[dict, list[dict]]:
    values = {}
    metadata = []
    parameters = parameters if parameters is not None else FIELDS[stream]
    expected = (
        {(number, name) for number in member_numbers for name in parameters}
        if member_numbers is not None else set(parameters)
    )
    grids = {}
    with path.open('rb') as handle:
        while (gid := eccodes.codes_grib_new_from_file(handle)) is not None:
            try:
                raw_name = eccodes.codes_get(gid, 'shortName')
                name = '10fg' if raw_name in ('10fg3', '10fg6') else raw_name
                number = int(eccodes.codes_get(gid, 'number')) if member_numbers is not None else None
                key = (number, name) if member_numbers is not None else name
                if key not in expected or key in values:
                    raise ValueError(f'{path.name}: campo inatteso o duplicato {name}')
                date = eccodes.codes_get(gid, 'dataDate')
                time = eccodes.codes_get(gid, 'dataTime')
                valid_date = eccodes.codes_get(gid, 'validityDate')
                valid_time = eccodes.codes_get(gid, 'validityTime')
                expected_valid = run + timedelta(hours=step)
                if (
                    date != int(run.strftime('%Y%m%d')) or time != run.hour * 100
                    or eccodes.codes_get(gid, 'endStep') != step
                    or valid_date != int(expected_valid.strftime('%Y%m%d'))
                    or valid_time != expected_valid.hour * 100
                    or eccodes.codes_get(gid, 'stepType') != ('max' if name in ('10fg', '10fg3', '10fg6') else 'instant')
                    or eccodes.codes_get(gid, 'stepUnits') != 1
                    or eccodes.codes_get(gid, 'edition') != 2
                    or eccodes.codes_get(gid, 'centre') != 'ecmf'
                    or eccodes.codes_get(gid, 'marsStream') != stream
                    or eccodes.codes_get(gid, 'marsType') != product
                ):
                    raise ValueError(f'{path.name}: run, scadenza o prodotto GRIB non corrispondenti')
                unit = eccodes.codes_get(gid, 'units')
                if unit != UNITS[name]:
                    raise ValueError(f'{path.name}: unita inattesa per {name}: {unit}')
                if (
                    eccodes.codes_get(gid, 'gridType') != 'regular_ll'
                    or eccodes.codes_get(gid, 'iDirectionIncrementInDegrees') != .25
                    or eccodes.codes_get(gid, 'jDirectionIncrementInDegrees') != .25
                ):
                    raise ValueError(f'{path.name}: attesa griglia regolare 0.25 gradi')
                if name in ('10u', '10v') and eccodes.codes_get(gid, 'uvRelativeToGrid') != 0:
                    raise ValueError(f'{path.name}: componenti vento non riferite a nord/est geografici')
                missing = eccodes.codes_get(gid, 'missingValue')
                sampled = []
                grid = eccodes.codes_get(gid, 'md5GridSection')
                if grid not in grids:
                    grids[grid] = [
                        eccodes.codes_grib_find_nearest(gid, p['lat'], p['lon'], npoints=1)[0]
                        for p in points
                    ]
                neighbours = grids[grid]
                field_values = eccodes.codes_get_double_elements(gid, 'values', [p['index'] for p in neighbours])
                for nearest, raw_value in zip(neighbours, field_values):
                    value = float(raw_value)
                    sampled.append({
                        'value': value if math.isfinite(value) and value != missing else None,
                        'grid_lat': float(nearest['lat']),
                        'grid_lon': (float(nearest['lon']) + 180) % 360 - 180,
                        'grid_distance_km': float(nearest['distance']),
                    })
                values[key] = sampled
                metadata.append({
                    'parameter': name, 'grib_short_name': raw_name, 'units': unit, 'run_utc': run.isoformat(),
                    'valid_utc': expected_valid.isoformat(), 'step_hours': step,
                    'stream': stream, 'type': product, 'member': number, 'grid_degrees': .25,
                    'start_step_hours': int(eccodes.codes_get(gid, 'startStep')),
                })
            finally:
                eccodes.codes_release(gid)
    if set(values) != expected:
        raise ValueError(f'{path.name}: campi mancanti: {sorted(expected - set(values))}')
    return values, metadata


def passage_check(run: datetime, departure: datetime, speed: float, points: list[dict], steps: list[int]) -> dict:
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('La velocita deve essere finita e maggiore di zero')
    arrival = departure + timedelta(hours=points[-1]['distance_nm'] / speed)
    end = run + timedelta(hours=360)
    bracketed = 0
    for point in points:
        lead = (departure + timedelta(hours=point['distance_nm'] / speed) - run).total_seconds() / 3600
        before = [s for s in steps if s <= lead]
        after = [s for s in steps if s >= lead]
        if before and after and min(after) - max(before) <= 6:
            bracketed += 1
    return {
        'departure_utc': departure.isoformat(), 'arrival_utc': arrival.isoformat(),
        'speed_kn': speed, 'route_nm': points[-1]['distance_nm'],
        'model_horizon_end_utc': end.isoformat(),
        'within_model_horizon': departure >= run and arrival <= end,
        'hours_arrival_beyond_horizon': max(0., (arrival - end).total_seconds() / 3600),
        'eta_points_bracketed_by_downloaded_steps': bracketed,
        'route_points': len(points),
        'note': 'Temporal bracketing only, not proof of valid wind/waves at every ETA. Sparse trial is not a passage forecast.',
    }


def probe(
    departure: datetime, speed: float = 5.5, steps: list[int] | None = None,
    run: datetime | None = None, source: str = 'ecmwf',
) -> Path:
    steps = sorted(set(steps if steps is not None else [0, 144, 360]))
    if not steps or len(steps) > 8 or any(s not in STEPS for s in steps):
        raise ValueError('Prova limitata a 1-8 scadenze: ogni 3 ore fino a 144, poi ogni 6 fino a 360')
    points = route(10)
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError('La velocita deve essere finita e maggiore di zero')
    client = Client(source=source, model='ifs', resol='0p25', infer_stream_keyword=False)
    latest_runs = {}
    if run is None:
        for stream in FIELDS:
            latest_runs[stream] = client.latest(stream=stream, type='fc', step=360).replace(tzinfo=timezone.utc)
        run = min(latest_runs.values())
    run = parse_run(run.isoformat())
    check = passage_check(run, departure, speed, points, steps)
    print(f'ECMWF IFS fc, run {run.isoformat()}, griglia 0.25 gradi; NON ensemble', flush=True)
    print(f"Fine orizzonte: {check['model_horizon_end_utc']} | Arrivo: {check['arrival_utc']}", flush=True)
    print('Prova sparsa: non modifica data/latest.json e non alimenta i grafici.', flush=True)
    folder = DATA / 'ecmwf' / run.strftime('%Y%m%dT%H%M%SZ')
    folder.mkdir(parents=True, exist_ok=True)
    rows, files = [], []
    for step in steps:
        decoded = {}
        for stream, parameters in FIELDS.items():
            path = folder / f'{stream}_{step:03d}.grib2'
            downloaded = not path.exists()
            if downloaded:
                temporary = path.with_suffix('.part')
                try:
                    client.retrieve(
                        date=run.strftime('%Y%m%d'), time=run.hour, stream=stream, type='fc',
                        step=step, param=list(parameters), target=str(temporary),
                    )
                    values, fields = decode_grib(temporary, run, step, stream, points)
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                values, fields = decode_grib(path, run, step, stream, points)
            decoded.update(values)
            files.append({
                'file': path.name, 'bytes': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'downloaded_this_invocation': downloaded, 'fields': fields,
            })
            print(f'{path.name}: {path.stat().st_size / 1_000_000:.2f} MB, campi verificati', flush=True)
        for i, point in enumerate(points):
            wind, direction = wind_from_uv(decoded['10u'][i]['value'], decoded['10v'][i]['value'])
            wave_direction = decoded['mwd'][i]['value']
            row = {
                'point_id': point['id'], 'sector': point['sector'],
                'lat': point['lat'], 'lon': point['lon'], 'distance_nm': point['distance_nm'],
                'course_deg': point['course_deg'], 'run_utc': run.isoformat(),
                'valid_utc': (run + timedelta(hours=step)).isoformat(), 'step_hours': step,
                'wind_kn': wind, 'wind_dir_deg': direction,
                'wind_rel_deg': relative_angle(direction, point['course_deg']),
                'wave_m': decoded['swh'][i]['value'], 'wave_dir_deg': wave_direction,
                'wave_period_s': decoded['mwp'][i]['value'],
                'wave_rel_deg': relative_angle(wave_direction, point['course_deg']),
            }
            for name in decoded:
                row.update({f'{name}_{key}': value for key, value in decoded[name][i].items() if key != 'value'})
            rows.append(row)
    manifest = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'source': source, 'dataset': 'ECMWF Open Data IFS', 'run_utc': run.isoformat(),
        'latest_runs_found': {k: v.isoformat() for k, v in latest_runs.items()},
        'resolution_degrees': .25, 'ensemble': False, 'steps_hours': steps,
        'files': files, 'total_bytes': sum(f['bytes'] for f in files),
        'downloaded_bytes': sum(f['bytes'] for f in files if f['downloaded_this_invocation']),
        'passage': check,
        'valid_wind_samples': sum(r['wind_kn'] is not None for r in rows),
        'valid_wave_samples': sum(r['wave_m'] is not None for r in rows),
        'warnings': [
            'Sparse fc/control trial only: no ensemble probabilities, no ETA interpolation.',
            'Global fields downloaded, route sampled at nearest grid point without shifting missing coastal waves to sea.',
            'Grid 0.25 degrees, not native 9 km. Missing values are unknown, not calm.',
            'Run is pinned across all fields; cached files are decoded and validated again.',
            'No gusts, pressure or separate swell in this bounded trial.',
            'Provisional route is NOT chart-validated; not a navigation or departure recommendation.',
        ],
        'attribution': 'ECMWF Open Data; https://www.ecmwf.int/en/forecasts/datasets/open-data',
    }
    tag = '-'.join(str(s) for s in steps)
    csv_path = folder / f'probe_{tag}.csv'
    csv_temp = csv_path.with_suffix('.tmp')
    pd.DataFrame(rows).to_csv(csv_temp, index=False)
    csv_temp.replace(csv_path)
    manifest_path = folder / f'probe_{tag}.json'
    temporary_manifest = manifest_path.with_suffix('.tmp')
    temporary_manifest.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    temporary_manifest.replace(manifest_path)
    print(f"Totale: {manifest['total_bytes'] / 1_000_000:.2f} MB; scaricati ora: {manifest['downloaded_bytes'] / 1_000_000:.2f} MB")
    print(f"Campioni vento: {manifest['valid_wind_samples']}/{len(rows)}; onde: {manifest['valid_wave_samples']}/{len(rows)}")
    print('Traversata entro orizzonte del modello:', check['within_model_horizon'])
    print(f"ETA racchiuse nelle scadenze scaricate: {check['eta_points_bracketed_by_downloaded_steps']}/{len(points)}")
    print('Manifest:', manifest_path)
    print('Campioni:', csv_path)
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--departure', required=True, help='Data partenza YYYY-MM-DD, alle 07 UTC')
    parser.add_argument('--speed', type=float, default=5.5)
    parser.add_argument('--steps', type=int, nargs='+', default=[0, 144, 360])
    parser.add_argument('--run', type=parse_run, help='Run esplicito, es. 2026-10-19T00:00Z')
    parser.add_argument('--source', choices=['ecmwf', 'aws', 'azure', 'google'], default='ecmwf')
    args = parser.parse_args()
    departure = datetime.strptime(args.departure, '%Y-%m-%d').replace(hour=7, tzinfo=timezone.utc)
    probe(departure, args.speed, args.steps, args.run, args.source)


if __name__ == '__main__':
    main()
