# Offshore Weather — Policastro → Genova

Local FastAPI application for **weather screening**, not nautical navigation or a safe-passage decision. The route is **provisional and not chart-validated**; do not load it into a plotter as a safe route. Inspect all legs on official charts before using this for a voyage.

## WSL setup

```bash
cd /mnt/d/meteo
source ~/venvs/meteo/bin/activate
unzip offshore_weather_app.zip
cd offshore_weather_app
pip install -r requirements.txt
cp ../.env .env     # optional, only for AI report; never commit .env
python engine.py --refresh
python report.py
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open **http://localhost:8000** in Windows. The frontend loads Leaflet, Plotly and OpenStreetMap tiles from public CDNs (internet required). If you don't need the AI report, skip `python report.py`.

### AI report: OpenAI HTTP 429

If `python report.py` reports `credit_balance_exhausted` or `insufficient_quota`, check the API credit balance and quota for the project/organization associated with your API key at https://platform.openai.com/settings/organization/billing/. ChatGPT subscriptions do not include API credit. Retrying immediately or changing the model does not resolve an exhausted balance. After resolving billing/quota, rerun only `python report.py`; no new weather download is needed.

Other HTTP 429 rate limits require waiting and checking the project's API limits. In both cases the script exits with an error without replacing any existing report. The weather dashboard and charts remain available without an AI report.

For a subsequent forecast run:

```bash
python engine.py --refresh
python report.py
```

Refresh the browser. Each run is archived in `data/runs/<UTC timestamp>/` and `data/latest.json` points to the latest. Without `--refresh`, cached API payloads are reused.

To change the departure dates or route sampling density:

```bash
python engine.py --refresh --step-nm 10 --departures 2026-10-17 2026-10-20 2026-10-30 2026-10-31 2026-11-01 2026-11-02 2026-11-03 --speeds 5 5.5 6
```

## Architecture

- `engine.py`: geographic interpolation, batched ensemble and marine API downloads, ETA sampling for coherent atmospheric members, CSV archives.
- `report.py`: optional OpenAI briefing from aggregated evidence, `.env` loaded from application directory.
- `app.py`: FastAPI backend, HTML report rendering and JSON endpoints.
- `static/index.html`: local dashboard, route map, spatial/temporal charts, per-point members and comparison table.
- `data/`: raw cached responses and run archives, created automatically.

## Terminal update with direct ECMWF data

The integrated update is manual: **no scheduler, background refresh or automatic OpenAI calls**. Run the command whenever you want fresh forecasts:

```bash
pip install -r requirements-grib.txt   # first-time optional GRIB dependencies
python update_ecmwf.py --departures 2026-10-30 2026-10-31 --speeds 5 5.5 6
```

The updater automatically selects the latest common run available through +360 hours for ECMWF IFS atmospheric control, perturbed ensemble and deterministic waves, then pins that initialization for every download. It uses all **51 atmospheric members** by default: `oper/fc` control (member 0) and `enfo/pf` members 1–50. Only native lead times bracketing the requested route-point ETAs are downloaded, with a maximum six-hour interpolation gap and no extrapolation beyond the model horizon.

It retrieves wind components, mean sea-level pressure and interval-maximum gusts, plus wave height, mean direction and mean period. Gusts are not instantaneous wind: the original GRIB interval start/end is recorded. There is no gust at step 0. Waves remain deterministic and are **not** ensemble probabilities; separate swell is unavailable in this workflow. Coastal cells with missing waves stay missing, and incomplete passages cannot acquire full-route wind exceedance frequencies.

**Check the plan first when choosing many dates.** Downloading selected fields from global GRIBs for 51 members can require several GB and substantial time; restricting dates saves much more than changing the route sampling step.

```bash
python update_ecmwf.py --departures 2026-10-30 2026-10-31 --speeds 5.5 --plan
```

`--plan` checks run availability and displays forecast coverage and a conservative size estimate without downloading GRIB payloads or changing the dashboard. The default budget is **20,000 MB of new GRIB payloads per invocation**; `--max-download-mb 4000` sets a smaller limit. The budget excludes index/HTTP overhead and is checked conservatively before each file and against actual downloaded size afterwards. A budget or network failure stops with an explicit error and leaves the active archive unchanged. Retry to reuse completed, validated files. Cached data are isolated by initialization, product, selected members and lead time; a new run never reuses old forecast values.

After the first update, this is sufficient to reuse the latest archive's departure dates (the speed default remains 5/5.5/6 kn):

```bash
python update_ecmwf.py
```

Use explicit `--departures` when changing travel dates. Dates outside the run's coverage are shown as incomplete/unknown, not silently moved. A request with no ETA within the model horizon fails rather than replacing the dashboard with an empty forecast.

The updater creates the existing CSV/meta archive format and switches `data/latest.json` **atomically only after the entire calculation and archive write succeed**. A Linux/WSL lock prevents two instances of this updater from running concurrently; do not run `engine.py` simultaneously. Existing archives are never overwritten. The newly active archive contains **direct ECMWF only**, not old GEFS forecasts presented as current. To return to the Open-Meteo/GEFS workflow, run `python engine.py --refresh` (its pre-existing raw-cache reuse behavior still applies).

Restart an already running backend once after installing this code. Then refresh the browser or click **Aggiorna vista** after an update: it detects a changed archive and reloads the controls and charts. The model selector is taken from archive metadata, and a source panel shows initialization, end of horizon, resolution, ensemble size, ETA coverage and missing waves. No new AI report is generated automatically; `python report.py` remains a separate optional, billed operation.

Additional options:

- `--run 2026-10-19T12:00Z`: pin a specific initialization; uncached fields must still exist in the remote rolling archive.
- `--source aws` (also `azure`, `google`): explicitly use an official mirror.
- `--no-publish`: calculate and persist a separate verification archive without switching the active dashboard.
- `--members 0 1 2`: small development subset; prominently labelled **subset**, not the full 51-member ensemble. Do not treat its frequencies as full-ensemble statistics.

Example bounded live validation, deliberately covering only the start of a late departure:

```bash
python update_ecmwf.py --run 2026-10-19T12:00Z --departures 2026-11-03 --speeds 6 --members 0 1 --no-publish --max-download-mb 100
```

This test is not a complete passage analysis. The implementation was checked against actual official control/perturbed/wave GRIBs, including a live **51-member** check at +354/+360 hours (approximately **356 MB** of new perturbed-member payloads, reusing the control/wave cache). Full-ensemble publication and failure safety are also exercised in offline tests. Full multi-GB passage downloads are left to the explicit terminal update.

## Static site on GitHub Pages

`export_static.py` turns the archive referenced by `data/latest.json` into a static site in `docs/` (`index.html` plus precomputed JSON: one file per departure/speed/model with the aggregated timeline and the per-member wind for each point). No server, API key or GRIB is published: `data/`, `.env`, and `.venv/` are in `.gitignore`. A typical export is about 10–15 MB.

Local update and publication, from the terminal:

```bash
python update_ecmwf.py --departures 2026-10-30 2026-10-31   # or python engine.py --refresh
python export_static.py --push                               # writes docs/, commits only docs/, and runs git push
```

`python export_static.py` without `--push` only regenerates `docs/`. To preview it: `python -m http.server -d docs 8000` and open `http://localhost:8000`. If nothing changed, no commit is created. Each push adds about a few MB to the git history.

GitHub Pages: *Settings → Pages → Deploy from a branch → `main` / `/docs`*. With a free GitHub plan the repository must be public, which also makes the page public: the warnings (route not validated, no GO/NO-GO) remain visible. The FastAPI dashboard (`uvicorn app:app`) keeps working as before.

## Direct ECMWF GRIB trial (optional)

A separate, bounded download command verifies ECMWF Open Data before any dashboard integration. It does **not** replace existing runs or modify `data/latest.json`. No Windy account, ECMWF API key, or OpenAI credit is needed.

```bash
pip install -r requirements-grib.txt
python ecmwf_grib.py --departure 2026-10-30 --speed 5.5
```

The command selects the latest common IFS atmospheric/wave run available at +360 hours, pins its initialization across all requests, and downloads only five global fields at three forecast lead times (0, 144, 360 hours):

- Atmospheric `oper/fc`: 10-metre U/V wind, converted from m/s to knots and meteorological **coming-from** direction.
- Marine `wave/fc`: significant combined wave height (`swh`), mean coming-from direction (`mwd`), and mean period (`mwp`).

This is a **single fc/control forecast, not an ensemble**. It cannot produce P10/P50/P90 or ensemble exceedance frequencies. There are no gust, pressure or separate swell fields in this first trial. The global Open Data grid used here is 0.25 degrees, not the native 9-km atmospheric grid.

Outputs go into `data/ecmwf/<initialization UTC>/`: validated GRIB2 files, a `probe_<steps>.csv` with route samples and grid-cell coordinates/distances for each field, and a matching JSON manifest with initialization, valid times, units, byte counts, SHA-256 checksums and arrival-horizon checks. GRIB metadata (including product, grid, units and forecast validity) is checked before a download becomes reusable. Repeated requests for the same run reuse and revalidate the files. Downloaded raw fields are retained, so the selected run remains available even after ECMWF's short rolling archive expires.

For an explicit run or a few other lead times:

```bash
python ecmwf_grib.py --run 2026-10-19T00:00Z --departure 2026-10-30 --steps 264 270 360
```

Explicit runs must still be available remotely unless already cached. The trial accepts at most eight lead times, every 3 hours through +144 and every 6 hours thereafter up to +360; use 00/12 UTC initializations. `--source aws`, `--source azure` and `--source google` explicitly select official Open Data mirrors if needed.

**Coverage is not the same as forecast quality or complete passage data.** The manifest distinguishes whether departure/arrival fall within the model's 360-hour horizon from whether the sparse downloaded steps bracket each route ETA within six hours. By default, the three widely separated steps do not cover the voyage at its ETAs. No interpolation across those large gaps is performed, and these trial samples are deliberately not offered in the dashboard model selector. Use `update_ecmwf.py` above for the integrated ensemble/ETA workflow.

Route sampling uses the nearest grid point and records its offset. Missing coastal wave cells remain missing, rather than being replaced by a more distant sea cell or by zero. This does not add coastal detail, validate the route, or establish safe departure conditions.

Verified live on 19 October 2026: the available common 00 UTC run provided wind and wave fields at +360 hours, valid until **3 November 2026 00 UTC**, using approximately **13.19 MB** for the default trial. Actual availability and file sizes vary by run.

Data and tooling: [ECMWF Open Data](https://www.ecmwf.int/en/forecasts/datasets/open-data), [official Python download client](https://github.com/ecmwf/ecmwf-opendata), and [ecCodes](https://github.com/ecmwf/eccodes-python). Keep ECMWF attribution when using the resulting data.

## Relative wind and wave angles

The dashboard includes an angle chart on the same distance/ETA samples as the wind-speed chart. Its vertical axis shows the incoming direction relative to the simulated course: 0° ahead, +90° starboard beam, -90° port beam, and ±180° astern, with indicative Italian labels for bolina, traverso and lasco on each tack. Wind is the unweighted circular mean of the selected atmospheric model's available member directions, not apparent wind or the direction of the median wind speed. Waves use the deterministic marine forecast. The simulated course is a proxy for heading; currents, leeway and boat polars are not modeled.

Missing directions and undefined circular means (opposing directions that cancel) remain gaps. Lines also break at the ±180° wrap to avoid suggesting a turn through the bow. Existing archives containing wind direction and course work without regenerating forecasts; restart the backend and reload the page after updating.

## Important limitations

- **Route not navigationally validated.** The initial corridor is an illustrative offshore polyline. It may cross land, hazards, traffic schemes or exclusion zones. Distance and ETA are provisional.
- Constant speed over ground (5 / 5.5 / 6 kn), no polars, currents, tides, deviations, port entries or weather-dependent speed.
- Atmospheric model members are evaluated consistently along each simulated passage; incomplete members are excluded from full-route exceedance calculations.
- Wave forecasts are deterministic or blended, **not paired wave ensemble members**. Wave coverage may end before the voyage. The dashboard does not claim wave probabilities.
- Forecasts are sampled along one line at 10 NM intervals; no lateral ±15 NM grid in this release.
- Open-Meteo may not support requested variables/model horizon; missing values must not be read as calm conditions. Review coverage and API responses.
- No automated background scheduling, real marine chart overlay, AIS, navigational hazard checking, or port refuge optimization in this release.
- API key billing is separate from ChatGPT; `.env` should be private.

## Data source

Open-Meteo Ensemble API: https://open-meteo.com/en/docs/ensemble-api
Open-Meteo Marine API: https://open-meteo.com/en/docs/marine-weather-api

For passage decisions consult official maritime forecasts, notices, charts and the skipper's judgment.
