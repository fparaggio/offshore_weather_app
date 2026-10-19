"""Generate AI narrative from aggregated numerical results, using .env if present."""
import argparse,json,os
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI,RateLimitError
from engine import DATA
load_dotenv(Path(__file__).resolve().parent/'.env')
p=argparse.ArgumentParser();p.add_argument('--model',default=os.getenv('OPENAI_MODEL','gpt-5.5'));a=p.parse_args()
if not (DATA/'latest.json').exists():raise SystemExit('First run python engine.py')
rid=json.loads((DATA/'latest.json').read_text())['run_id'];folder=DATA/'runs'/rid
meta=json.loads((folder/'meta.json').read_text())
comparison=pd.read_csv(folder/'comparison.csv').replace({float('inf'):None,float('-inf'):None})
coverage=pd.read_csv(folder/'coverage.csv')
samples=pd.read_csv(folder/'samples.csv',low_memory=False)
# Group by actual ETA and route location; never combine member percentiles as trajectories.
group=samples.groupby(['departure','speed_kn','model','point_id','sector','navigation_day','eta_utc'],dropna=False).agg(
    n_wind=('wind_kn','count'),wind_p50=('wind_kn','median'),wind_p90=('wind_kn',lambda x:x.quantile(.9)),
    gust_p90=('gust_kn',lambda x:x.quantile(.9)),wave_m=('wave_m','median'),
    wave_period_s=('wave_period_s','median')).reset_index()
# Limit to nominal speed and all departures, keeping token usage predictable.
group=group[group.speed_kn==5.5]
package={'metadata':meta,'comparison':comparison.where(pd.notna(comparison),None).to_dict('records'),
         'coverage_summary':coverage.groupby(['type','model']).agg(points=('point','count'),min_hours=('hours','min'),max_hours=('hours','max')).reset_index().to_dict('records'),
         'route_timed':group.where(pd.notna(group),None).to_dict('records')}
def scrub(x):
 if isinstance(x,dict):return {str(k):scrub(v) for k,v in x.items()}
 if isinstance(x,list):return [scrub(v) for v in x]
 if isinstance(x,float) and not __import__('math').isfinite(x):return None
 if hasattr(x,'item'):return scrub(x.item())
 return x
package=scrub(package)
(folder/'ai_evidence.json').write_text(json.dumps(package,ensure_ascii=False,allow_nan=False,default=str,indent=2))
client=OpenAI()
try:
 response=client.responses.create(model=a.model,store=False,max_output_tokens=4500,
 instructions='''Sei un analista meteo-marino per pianificazione di una traversata offshore su Grand Soleil 35.
Scrivi in italiano Markdown professionale con sezioni: copertura dati, sintesi, confronto partenze dal 30 ottobre 2026,
analisi per giornata di navigazione (24h continuative, non tappe con sosta), criticità per settore, incertezza, limiti e verifiche prima di partire.
Usa ESCLUSIVAMENTE i dati forniti; nessuna invenzione di previsioni, porti accessibili, condizioni marine o valori.
Distingui onde deterministiche e membri ensemble atmosferici, mancanza dati, campioni incompleti e frequenze empiriche non calibrate.
Se dati insufficienti, segnala NON VALUTABILE; nessun GO/NO-GO definitivo.
La rotta è un corridoio geografico preliminare NON validato su carta nautica; non suggerire di navigarla come tale.
Considera l'arrivo in Mar Ligure e la navigazione notturna. Rimanda a bollettini ufficiali e decisione skipper.''',
 input=json.dumps(package,ensure_ascii=False,allow_nan=False,default=str))
except RateLimitError as exc:
 if exc.code in ('credit_balance_exhausted','insufficient_quota') or exc.type=='insufficient_quota':
  raise SystemExit(
   'Report AI non generato: credito o quota API OpenAI esauriti. '
   'Verifica credito e limiti del progetto/organizzazione associati alla chiave API: '
   'https://platform.openai.com/settings/organization/billing/ . '
   "L'abbonamento ChatGPT non include il credito API. "
   'Dopo aver risolto, riesegui python report.py. '
   'Dashboard e grafici meteo restano utilizzabili; eventuali report precedenti non sono stati modificati.'
  ) from None
 raise SystemExit(
  'Report AI non generato: limite di richieste OpenAI raggiunto (HTTP 429). '
  'Riprova piu tardi e verifica i limiti API del progetto. '
  'Eventuali report precedenti non sono stati modificati.'
 ) from None
if not response.output_text:raise RuntimeError('Empty model response')
(folder/'report_ai.md').write_text('# Briefing automatico — Policastro → Genova\n\n'+response.output_text,encoding='utf-8')
print('Report:',folder/'report_ai.md')
