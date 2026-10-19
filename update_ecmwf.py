"""Download a pinned ECMWF ensemble/wave run and atomically publish dashboard CSVs."""
from __future__ import annotations

import argparse
import bisect
import fcntl
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

import pandas as pd
from ecmwf.opendata import Client

from ecmwf_grib import STEPS, KNOTS_PER_MS, decode_grib, parse_run, passage_check, wind_from_uv
from engine import DATA, calculate, route

MODEL = 'ecmwf_ifs025_direct'
LABEL = 'ECMWF ENS diretto'


def required_steps(run, points, departures, speeds):
    """Union of native lead times bracketing each in-horizon route ETA."""
    selected = set()
    for departure in departures:
        start = datetime.strptime(departure, '%Y-%m-%d').replace(hour=7, tzinfo=timezone.utc)
        for speed in speeds:
            for point in points:
                hours = (start - run).total_seconds() / 3600 + point['distance_nm'] / speed
                if 0 <= hours <= 360:
                    index = bisect.bisect_left(STEPS, hours)
                    selected.add(STEPS[index])
                    if STEPS[index] != hours:
                        selected.add(STEPS[index - 1])
    if not selected:
        raise ValueError('Nessuna ETA nell\'orizzonte del run: cambia partenze o run.')
    return sorted(selected)


def read_product(client, folder, run, step, stream, product, numbers, points, budget):
    parameters = ('swh', 'mwd', 'mwp') if stream == 'wave' else (
        ('10u', '10v', 'msl', '10fg') if step else ('10u', '10v', 'msl')
    )
    tag = '-'.join(map(str, numbers)) if numbers else 'control'
    path = folder / f'{stream}_{product}_{tag}_{step:03d}.grib2'
    decoding = dict(product=product, parameters=parameters, member_numbers=numbers if product == 'pf' else None)
    downloaded = not path.exists()
    if downloaded:
        # A global member with these four fields is normally a few MB.
        estimate = (len(numbers) if numbers else 1) * 8_000_000
        if budget['bytes'] + estimate > budget['limit']:
            raise ValueError('Budget download raggiunto: aumenta --max-download-mb e riprendi; cache conservata.')
        temporary = path.with_suffix('.part')
        request = dict(date=run.strftime('%Y%m%d'), time=run.hour, step=step,
                       stream=stream, type=product, param=list(parameters), target=str(temporary))
        if product == 'pf':
            request['number'] = list(numbers)
        try:
            client.retrieve(**request)
            values, fields = decode_grib(temporary, run, step, stream, points, **decoding)
            budget['bytes'] += temporary.stat().st_size
            if budget['bytes'] > budget['limit']:
                raise ValueError('Budget download superato: prodotto non pubblicato; aumenta --max-download-mb.')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        values, fields = decode_grib(path, run, step, stream, points, **decoding)
    print(f'{path.name}: {path.stat().st_size / 1e6:.2f} MB {"scaricato" if downloaded else "cache verificata"}', flush=True)
    return values, {
        'file': str(path.relative_to(DATA)), 'bytes': path.stat().st_size,
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'fields': fields,
    }


def frames_from_products(points, products, run, numbers):
    atmospheric = {p['id']: [] for p in points}
    marine = {p['id']: [] for p in points}
    for step, (members, waves) in sorted(products.items()):
        valid = pd.Timestamp(run + timedelta(hours=step))
        for i, point in enumerate(points):
            atmosphere = {'time': valid}
            for number in numbers:
                fields = members[number]
                u, v = fields['10u'][i]['value'], fields['10v'][i]['value']
                wind, direction = wind_from_uv(u, v)
                suffix = f'_member{number:02d}'
                atmosphere['wind_speed_10m' + suffix] = wind
                atmosphere['wind_direction_10m' + suffix] = direction
                pressure = fields['msl'][i]['value']
                atmosphere['pressure_msl' + suffix] = pressure / 100 if pressure is not None else None
                gust = fields['10fg'][i]['value'] if '10fg' in fields else None
                atmosphere['wind_gusts_10m' + suffix] = gust * KNOTS_PER_MS if gust is not None else None
            atmospheric[point['id']].append(atmosphere)
            marine[point['id']].append({
                'time': valid, 'wave_height': waves['swh'][i]['value'],
                'wave_direction': waves['mwd'][i]['value'], 'wave_period': waves['mwp'][i]['value'],
            })
    atm, sea = {}, {}
    coverage = []
    for point in points:
        pid = point['id']
        for kind, records, target in [('atmosphere', atmospheric[pid], atm), ('marine', marine[pid], sea)]:
            frame = pd.DataFrame(records).set_index('time').sort_index()
            frame.index = pd.to_datetime(frame.index, utc=True).as_unit('ns')
            target[(MODEL, pid) if kind == 'atmosphere' else pid] = frame
            coverage.append({
                'type': kind, 'model': MODEL if kind == 'atmosphere' else 'ECMWF wave fc (non ensemble)',
                'point': pid, 'hours': len(frame), 'first': frame.index.min().isoformat(),
                'last': frame.index.max().isoformat(), 'run_utc': run.isoformat(),
                'members_first': len(numbers) if kind == 'atmosphere' else 1,
                'valid_values': int(frame.notna().sum().sum()),
            })
    return atm, sea, coverage


def publish(points, samples, scenarios, comparison, coverage, meta, publish_latest=True):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ_ecmwf')
    folder = DATA / 'runs' / stamp
    folder.mkdir(parents=True)
    for name, frame in [
        ('route_points', pd.DataFrame(points)), ('samples', samples), ('scenarios', scenarios),
        ('comparison', comparison), ('coverage', pd.DataFrame(coverage)),
    ]:
        frame.to_csv(folder / f'{name}.csv', index=False)
    meta = dict(meta, run_id=stamp, generated_utc=datetime.now(timezone.utc).isoformat())
    (folder / 'meta.json').write_text(json.dumps(meta, indent=2, allow_nan=False), encoding='utf-8')
    if publish_latest:
        temporary = DATA / 'latest.ecmwf.tmp'
        temporary.write_text(json.dumps({'run_id': stamp}), encoding='utf-8')
        temporary.replace(DATA / 'latest.json')
    return folder


def update(departures, speeds=(5., 5.5, 6.), numbers=tuple(range(51)), run=None,
           source='ecmwf', step_nm=10., max_download_mb=20000., plan=False, publish_latest=True):
    if not departures or not speeds or any(not math.isfinite(s) or s <= 0 for s in speeds):
        raise ValueError('Indica partenze e velocita finite maggiori di zero.')
    if not numbers or len(set(numbers)) != len(numbers) or any(n not in range(51) for n in numbers):
        raise ValueError('Membri distinti da 0 (controllo) a 50.')
    if not math.isfinite(step_nm) or step_nm <= 0 or not math.isfinite(max_download_mb) or max_download_mb <= 0:
        raise ValueError('Passo e budget devono essere finiti e positivi.')
    points = route(step_nm)
    client = Client(source=source, model='ifs', resol='0p25', infer_stream_keyword=False)
    if run is None:
        candidates = [
            client.latest(stream=stream, type=kind, step=360).replace(tzinfo=timezone.utc)
            for stream, kind in [('oper', 'fc'), ('enfo', 'pf'), ('wave', 'fc')]
        ]
        run = min(candidates)
    run = parse_run(run.isoformat())
    steps = required_steps(run, points, departures, speeds)
    checks = [
        passage_check(run, datetime.strptime(d, '%Y-%m-%d').replace(hour=7, tzinfo=timezone.utc), s, points, steps)
        for d in departures for s in speeds
    ]
    print(f'Run ECMWF: {run.isoformat()} | {len(numbers)} membri | {len(steps)} scadenze | {len(points)} punti', flush=True)
    print(f'Partenze: {", ".join(departures)} | velocita: {speeds}', flush=True)
    print(f'Stima prudenziale senza cache: {len(steps) * (len(numbers) + 1) * 8:.0f} MB; limite: {max_download_mb:.0f} MB', flush=True)
    for check in checks:
        print(f"{check['departure_utc']} @ {check['speed_kn']} kn: arrivo {check['arrival_utc']}; entro orizzonte: {check['within_model_horizon']}", flush=True)
    if plan:
        return None
    folder = DATA / 'ecmwf' / run.strftime('%Y%m%dT%H%M%SZ') / 'ensemble-v1'
    folder.mkdir(parents=True, exist_ok=True)
    products, provenance = {}, []
    budget = {'bytes': 0, 'limit': max_download_mb * 1e6}
    for step in steps:
        members = {}
        if 0 in numbers:
            fields, record = read_product(client, folder, run, step, 'oper', 'fc', None, points, budget)
            members[0] = fields
            provenance.append(record)
        perturbed = tuple(n for n in numbers if n != 0)
        if perturbed:
            fields, record = read_product(client, folder, run, step, 'enfo', 'pf', perturbed, points, budget)
            for n in perturbed:
                members[n] = {name: value for (member, name), value in fields.items() if member == n}
            provenance.append(record)
        waves, record = read_product(client, folder, run, step, 'wave', 'fc', None, points, budget)
        provenance.append(record)
        products[step] = (members, waves)
    atm, sea, coverage = frames_from_products(points, products, run, numbers)
    samples, scenarios, comparison = calculate(points, atm, sea, departures, speeds, models=[MODEL])
    if samples.empty or not samples.wind_kn.notna().any():
        raise ValueError('Nessun campione vento valido: archivio attivo non modificato.')
    full = set(numbers) == set(range(51))
    meta = {
        'source': 'ECMWF Open Data IFS ENS + wave fc', 'model_run_utc': run.isoformat(),
        'forecast_end_utc': (run + timedelta(hours=360)).isoformat(),
        'models': [{'id': MODEL, 'label': LABEL + ('' if full else f' (sottoinsieme {len(numbers)}/51)')}],
        'ensemble_members': list(numbers), 'full_ensemble': full, 'marine_ensemble': False,
        'resolution_degrees': .25, 'route_nm': points[-1]['distance_nm'], 'route_validated': False,
        'departure_hour_utc': 7, 'departures': list(departures), 'speeds_kn': list(speeds),
        'sampling_step_nm': step_nm, 'steps_hours': steps, 'passage_checks': checks,
        'downloaded_bytes': budget['bytes'], 'grib_files': provenance,
        'warnings': [
            'ECMWF Open Data 0.25 degrees; nearest grid cell, no coastal downscaling.',
            'Member 0 is the oper/fc control; members 1-50 are enfo/pf, same initialization.',
            'Waves are deterministic wave/fc, not paired wave ensemble members. No separate swell.',
            'Gusts are interval maxima between GRIB startStep/endStep, interpolated between valid times; step 0 has no gust.',
            'Only in-horizon ETAs are sampled; missing values and coastal wave cells stay unknown.',
            'Percentiles and frequencies refer only to selected atmospheric members; inspect full_ensemble.',
            'Route is provisional, NOT chart-validated. No GO/NO-GO or navigational guarantee.',
        ],
        'attribution': 'ECMWF Open Data https://www.ecmwf.int/en/forecasts/datasets/open-data',
    }
    destination = publish(points, samples, scenarios, comparison, coverage, meta, publish_latest)
    print(f"Archivio {'pubblicato' if publish_latest else 'di verifica, NON pubblicato'}: {destination}", flush=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--departures', nargs='+', help='YYYY-MM-DD; default: date dell ultimo archivio')
    parser.add_argument('--speeds', nargs='+', type=float, default=[5., 5.5, 6.])
    parser.add_argument('--members', nargs='+', type=int, default=list(range(51)))
    parser.add_argument('--run', type=parse_run)
    parser.add_argument('--source', choices=['ecmwf', 'aws', 'azure', 'google'], default='ecmwf')
    parser.add_argument('--step-nm', type=float, default=10.)
    parser.add_argument('--max-download-mb', type=float, default=20000.)
    parser.add_argument('--plan', action='store_true', help='Mostra piano senza scaricare GRIB o pubblicare')
    parser.add_argument('--no-publish', action='store_true', help='Crea archivio ma non cambia dashboard attiva')
    args = parser.parse_args()
    departures = args.departures
    if departures is None:
        latest = DATA / 'latest.json'
        if not latest.exists():
            parser.error('Primo aggiornamento: specifica --departures YYYY-MM-DD ...')
        rid = json.loads(latest.read_text())['run_id']
        departures = json.loads((DATA / 'runs' / rid / 'meta.json').read_text())['departures']
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'ecmwf-update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, 'Un altro aggiornamento ECMWF e gia in corso.\n')
        update(departures, tuple(args.speeds), tuple(args.members), args.run, args.source,
               args.step_nm, args.max_download_mb, args.plan, not args.no_publish)


if __name__ == '__main__':
    main()
