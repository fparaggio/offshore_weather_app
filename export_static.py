"""Export the latest archive as a static site (GitHub Pages) and optionally push it.

    python export_static.py            # write docs/
    python export_static.py --push     # write docs/, commit docs/ and git push
"""
from __future__ import annotations
import argparse, json, math, shutil, subprocess, sys, tempfile
from pathlib import Path
import pandas as pd
from app import ROOT, aggregate_timeline, clean, report_html
from engine import DATA

POINT_FIELDS = ('member', 'wind_kn')


def speed_key(speed) -> str:
    return f'{float(speed):g}'


def combo_name(departure, speed, model) -> str:
    return f'{departure}_{speed_key(speed)}_{model}.json'


def rounded(value, digits=2):
    if isinstance(value, dict):
        return {k: rounded(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [rounded(v, digits) for v in value]
    if isinstance(value, float):
        return round(value, digits) if math.isfinite(value) else None
    return value


def dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rounded(clean(payload)), ensure_ascii=False, separators=(',', ':')))


def read(folder: Path, name: str) -> pd.DataFrame:
    path = folder / name
    return pd.read_csv(path, low_memory=False) if path.exists() and path.stat().st_size else pd.DataFrame()


def latest_archive() -> Path:
    pointer = DATA / 'latest.json'
    if not pointer.exists():
        raise SystemExit('Nessun archivio: esegui prima update_ecmwf.py o engine.py.')
    folder = DATA / 'runs' / json.loads(pointer.read_text())['run_id']
    if not folder.exists():
        raise SystemExit(f'Archivio non trovato: {folder}')
    return folder


def export(folder: Path, out: Path) -> dict:
    meta = json.loads((folder / 'meta.json').read_text())
    tmp = Path(tempfile.mkdtemp(prefix='.static-', dir=out.parent if out.parent.exists() else None))
    try:
        data = tmp / 'data'
        dump(data / 'meta.json', meta)
        dump(data / 'route.json', read(folder, 'route_points.csv').to_dict('records'))
        dump(data / 'coverage.json', read(folder, 'coverage.csv').to_dict('records'))
        dump(data / 'comparison.json', read(folder, 'comparison.csv').to_dict('records'))
        (data / 'report.html').write_text(report_html(folder))
        samples = read(folder, 'samples.csv')
        combos = []
        if not samples.empty:
            for (departure, speed, model), group in samples.groupby(['departure', 'speed_kn', 'model'], sort=False):
                points = {pid: [{k: r[k] for k in POINT_FIELDS} for r in g[list(POINT_FIELDS)].to_dict('records')]
                          for pid, g in group.groupby('point_id', sort=False)}
                dump(data / 'combo' / combo_name(departure, speed, model),
                     {'timeline': aggregate_timeline(group), 'points': points})
                combos.append(combo_name(departure, speed, model))
        dump(data / 'index.json', {'run_id': meta.get('run_id'), 'combos': combos})
        page = (ROOT / 'static' / 'index.html').read_text()
        marker = '<meta charset="utf-8">'
        if marker not in page:
            raise SystemExit('static/index.html: meta charset non trovato')
        (tmp / 'index.html').write_text(page.replace(marker, marker + '<meta name="static-data" content="data/">', 1))
        (tmp / '.nojekyll').write_text('')
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    size = sum(p.stat().st_size for p in out.rglob('*') if p.is_file())
    return {'run_id': meta.get('run_id'), 'combos': len(combos), 'bytes': size}


def git(*args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(['git', *args], cwd=ROOT, check=check, text=True, capture_output=True)


def push(out: Path, run_id: str, message: str | None) -> None:
    if git('rev-parse', '--is-inside-work-tree', check=False).returncode:
        raise SystemExit('Non è un repository git: vedi README, sezione GitHub Pages.')
    rel = out.relative_to(ROOT).as_posix()
    git('add', '-A', rel)
    if not git('diff', '--cached', '--quiet', '--', rel, check=False).returncode:
        print('Nessuna modifica da pubblicare.')
        return
    git('commit', '-m', message or f'Aggiornamento meteo {run_id}', '--', rel)
    result = git('push', check=False)
    if result.returncode:
        raise SystemExit('git push fallito:\n' + result.stderr)
    print('Push completato. GitHub Pages si aggiorna in 1-2 minuti.')


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, default=ROOT / 'docs')
    parser.add_argument('--archive', type=Path, help='cartella archivio (default: data/latest.json)')
    parser.add_argument('--push', action='store_true', help='commit di docs/ e git push')
    parser.add_argument('--message', help='messaggio di commit')
    a = parser.parse_args(argv)
    out = a.out.resolve()
    info = export(a.archive or latest_archive(), out)
    print(f"Esportato {info['run_id']}: {info['combos']} combinazioni, {info['bytes'] / 1e6:.1f} MB in {out}")
    if a.push:
        push(out, info['run_id'], a.message)


if __name__ == '__main__':
    main(sys.argv[1:])
