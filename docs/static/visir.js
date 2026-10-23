/* Original VISIR data are independent of constant-speed weather screening. */
(() => {
  const colors = ['#ffb454', '#9bafff', '#64e4bb', '#f793c6', '#78d9ef', '#e4dd73'];
  const propulsionColors = {motor: '#ff9800', sail: '#35c875', unknown: '#9ca3af'};
  const propulsionColor = p => propulsionColors[p.propulsion_mode] || propulsionColors.unknown;
  const chartIds = ['visir-sog', 'visir-wind', 'visir-wave'];
  const fields = ['SOG', 'wind_magn', 'Hs'];
  const units = ['kn', 'kn', 'm'];
  const metrics = [
    {field: 'wind_magn', label: 'Vento', unit: 'kn'},
    {field: 'gust_kn', label: 'Raffica', unit: 'kn'},
    {field: 'Hs', label: 'Onda significativa (Hs)', unit: 'm'},
    {field: 'swell_m', label: 'Swell', unit: 'm'},
    {field: 'wave_period_s', label: 'Periodo onda', unit: 's'},
    {field: 'SOG', label: 'Velocita sul fondo (SOG)', unit: 'kn'},
    {field: 'wind_dir', label: 'Direzione vento (convenzione VISIR)', unit: 'gradi', angle: true},
    {field: 'Th_wave', label: 'Direzione onda Th_wave (convenzione VISIR)', unit: 'gradi', angle: true},
    {field: 'COG', label: 'Rotta sul fondo (COG)', unit: 'gradi', angle: true},
    {field: 'HDG', label: 'Prua (HDG)', unit: 'gradi', angle: true},
    {field: 'gamma_i', label: 'Vento relativo esportato (gamma_i)', unit: 'gradi', angle: true},
    {field: 'gamma_a', label: 'Onde relative esportate (gamma_a)', unit: 'gradi', angle: true},
    {field: 'cumTime', label: 'Tempo cumulato', unit: 'h'},
  ];
  for (const variable of ['wind', 'gust']) {
    for (const q of [10, 50, 90]) {
      metrics.push({field: `${variable}_kn_p${q}`, label: `ECMWF ${variable === 'wind' ? 'vento' : 'raffica intervallo'} P${q}`,
        unit: 'kn', source: 'ecmwf'});
    }
  }
  metrics.push(
    {field: 'wave_m', label: 'ECMWF Hs deterministica', unit: 'm', source: 'ecmwf'},
    {field: 'wave_period_s', label: 'ECMWF periodo medio onda', unit: 's', source: 'ecmwf'},
    {field: 'swell_m', label: 'ECMWF swell totale Hs', unit: 'm', source: 'ecmwf'},
    {field: 'swell_period_s', label: 'ECMWF periodo medio swell', unit: 's', source: 'ecmwf'},
    {field: 'wind_dir_from_deg', label: 'ECMWF vento FROM medio circolare', unit: 'gradi', angle: true, source: 'ecmwf'},
    {field: 'wave_dir_from_deg', label: 'ECMWF onde FROM', unit: 'gradi', angle: true, source: 'ecmwf'},
    {field: 'wind_rel_hdg_deg', label: 'ECMWF vento rispetto a prua HDG', unit: 'gradi', angle: true, source: 'ecmwf'},
    {field: 'wave_rel_hdg_deg', label: 'ECMWF onde rispetto a prua HDG', unit: 'gradi', angle: true, source: 'ecmwf'},
    {field: 'swell_rel_hdg_deg', label: 'ECMWF swell rispetto a prua HDG', unit: 'gradi', angle: true, source: 'ecmwf'}
  );
  const metricKey = m => m.source === 'ecmwf' ? 'ecmwf:' + m.field : m.field;
  const analysisCache = new Map();
  let staticAnalyses, analysisDownloadUrl, analyzing = false;
  const analysisFor = r => analysisCache.get(r.id);
  const metricValue = (r, i, m) => (m.source === 'ecmwf' ? analysisFor(r)?.waypoints[i] : r.waypoints[i])?.[m.field];
  const hasValue = value => typeof value === 'number' && Number.isFinite(value);
  const linkedCharts = () => $('visir-chart-x').value === 'time' ? [...chartIds, 'visir-explorer'] : chartIds;
  const rome = new Intl.DateTimeFormat('it-IT', {
    timeZone: 'Europe/Rome', dateStyle: 'short', timeStyle: 'medium'
  });
  const zone = new Intl.DateTimeFormat('it-IT', {
    timeZone: 'Europe/Rome', timeZoneName: 'short'
  });
  const localTime = t => t ? rome.format(new Date(t)) + ' ' +
    zone.formatToParts(new Date(t)).find(p => p.type === 'timeZoneName').value + ' (Europe/Rome)' : 'non disponibile';
  const arrivalFormat = new Intl.DateTimeFormat('it-IT', {
    timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', hourCycle: 'h23'
  });
  const arrivalTime = t => {
    if (!t) return 'non disponibile';
    const rounded = new Date(Math.round(Date.parse(t) / 3600000) * 3600000);
    return '~ ' + arrivalFormat.format(rounded) + ' ' +
      zone.formatToParts(rounded).find(p => p.type === 'timeZoneName').value + ' (Europe/Rome)';
  };
  const label = r => `${r.model} / ${r.optimization === 'time' ? 'minimo tempo' : 'minima distanza'} / ${r.run} / ${r.departure_key}`;
  const overlay = L.layerGroup();
  const highlight = L.circleMarker([0, 0], {radius: 9, color: '#ffffff', weight: 3, fillColor: '#ff584d', fillOpacity: 1});
  let routes = [], selectedId = null, chartSync = false;
  let chartGeneration = 0;
  const selectedRoute = () => routes.find(r => r.id === selectedId);
  const colorFor = r => colors[routes.indexOf(r) % colors.length];
  function status(message, error = false) {
    $('visir-status').textContent = message;
    $('visir-status').dataset.error = String(error);
  }
  function detail(p, interpolated = false) {
    return `${interpolated ? 'Posizione interpolata (non un waypoint)' : 'Waypoint ' + p.waypoint_ID}
${localTime(p.ISO_date)}${p.ISO_date ? ' | ' + p.ISO_date : ''}
Posizione: ${val(p.lat, 6)}, ${val(p.lon, 6)}
Distanza cumulata: ${val(p.cumDist, 3)} NM | Tempo cumulato: ${val(p.cumTime, 3)} h
SOG: ${val(p.SOG, 2)} kn | Vento: ${val(p.wind_magn, 2)} kn | wind_dir: ${val(p.wind_dir, 1)}° | Hs: ${val(p.Hs, 2)} m
COG: ${val(p.COG, 1)}° | HDG: ${val(p.HDG, 1)}° | Th_wave: ${val(p.Th_wave, 1)}° | psi_a: ${val(p.psi_a, 1)}°`;
  }
  function mark(p, interpolated = false) {
    highlight.setLatLng([p.lat, p.lon]);
    if (!overlay.hasLayer(highlight)) overlay.addLayer(highlight);
    const text = detail(p, interpolated);
    const propulsionText = !interpolated && p.propulsion_mode != null ?
      `\nPropulsione segmento uscente (VISIR): ${p.propulsion_mode === 'motor' ? 'motore' : p.propulsion_mode === 'sail' ? 'vela' : p.propulsion_mode}` : '';
    const r = selectedRoute();
    const index = interpolated ? -1 : r?.waypoints.findIndex(w => w.waypoint_ID === p.waypoint_ID);
    const a = index >= 0 ? analysisFor(r)?.waypoints[index] : null;
    const extra = a ? `\nNuova analisi ECMWF (non VISIR): vento P50 ${val(a.wind_kn_p50)} kn (${a.wind_n}/51 membri), raffica P90 ${val(a.gust_kn_p90)} kn (${a.gust_n}/51).
Periodo onda ${val(a.wave_period_s)} s | Swell ${val(a.swell_m)} m
Vento relativo HDG ${val(a.wind_rel_hdg_deg)}° | Onde relative HDG ${val(a.wave_rel_hdg_deg)}° | Concordanza vento ${val(a.wind_dir_agreement == null ? null : a.wind_dir_agreement * 100, 0)}%` : '';
    $('visir-detail').textContent = text + propulsionText + extra;
    const box = document.createElement('div');
    box.style.whiteSpace = 'pre-line';
    box.textContent = text + propulsionText + extra;
    highlight.bindTooltip(box, {direction: 'top'});
    if ($('visir-explorer').data) {
      const x = $('visir-chart-x').value === 'distance' ? p.cumDist : p.ISO_date;
      Plotly.relayout('visir-explorer', {shapes: x == null ? [] : [{
        type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x,
        y0: 0, y1: 1, line: {color: '#ffffff', width: 1, dash: 'dot'}
      }]});
    }
    if (p.ISO_date && selectedRoute()?.timed) {
      const points = selectedRoute().waypoints;
      const first = Date.parse(points[0].ISO_date), last = Date.parse(points.at(-1).ISO_date);
      $('visir-clock').value = String(1000 * (Date.parse(p.ISO_date) - first) / (last - first));
      for (const id of chartIds) {
        if (!$(id).data) continue;
        Plotly.relayout(id, {shapes: [{
          type: 'line', xref: 'x', yref: 'paper', x0: p.ISO_date, x1: p.ISO_date,
          y0: 0, y1: 1, line: {color: '#ffffff', width: 1, dash: 'dot'}
        }]});
      }
    }
  }
  function drawMap() {
    overlay.clearLayers();
    $('visir-clock').disabled = !selectedRoute()?.timed;
    $('visir-detail').textContent = '';
    for (const r of routes.filter(r => r.id === selectedId)) {
      for (let i = 0; i < r.waypoints.length - 1; i++) {
        const start = r.waypoints[i], end = r.waypoints[i + 1];
        L.polyline([[start.lat, start.lon], [end.lat, end.lon]], {
          color: propulsionColor(start), weight: 5, opacity: .95
        }).on('click', () => mark(start)).addTo(overlay);
      }
      r.waypoints.forEach((p, i) => {
        const marker = L.circleMarker([p.lat, p.lon], {
          color: propulsionColor(p), radius: i === 0 || i === r.waypoints.length - 1 ? 7 : 3,
          fillOpacity: 1, weight: 1
        });
        const box = document.createElement('div');
        box.style.whiteSpace = 'pre-line';
        box.textContent = `${i === 0 ? 'Partenza' : i === r.waypoints.length - 1 ? 'Arrivo' : 'Passaggio'}\n${label(r)}\n${detail(p)}`;
        marker.bindTooltip(box).on('mouseover', () => {
          if (r.id === selectedId) mark(p);
        }).on('click', () => {
          if (selectedId !== r.id) {
            selectedId = r.id;
            $('visir-select').value = r.id;
            showSelected();
          }
          mark(p);
        }).addTo(overlay);
      });
    }
    if (selectedRoute()) mark(selectedRoute().waypoints[0]);
  }
  function fit() {
    if (!$('map').clientWidth || !$('map').clientHeight) return;
    const points = selectedRoute()?.waypoints.map(p => [p.lat, p.lon]) || [];
    if (points.length) map.fitBounds(points, {padding: [25, 25], animate: false});
  }
  function statistics(r) {
    const points = r.waypoints;
    const peak = (rows, field) => {
      const values = rows.map(p => p[field]).filter(hasValue);
      return {value: values.length ? Math.max(...values) : null, count: values.length};
    };
    const wind = peak(points, 'wind_magn'), waves = peak(points, 'Hs'), sog = peak(points, 'SOG');
    const a = analysisFor(r);
    const gust = peak(a?.waypoints || [], 'gust_kn_p90');
    const meanSpeed = hasValue(r.distance_nm) && hasValue(r.duration_hours) && r.duration_hours > 0 ?
      r.distance_nm / r.duration_hours : null;
    const cards = [
      ['Distanza', val(r.distance_nm, 2) + ' NM', 'Cumulata originale VISIR'],
      ['Durata', val(r.duration_hours, 2) + ' h', 'Cumulata originale VISIR'],
      ['Partenza', localTime(r.departure_utc), 'Timestamp originale VISIR'],
      ['Arrivo', arrivalTime(r.arrival_utc), 'Indicativo, arrotondato all’ora piu vicina'],
      ['Waypoint', String(points.length), 'Coordinate effettive del grafo'],
      ['Velocita media viaggio', val(meanSpeed, 2) + ' kn', 'Distanza / durata VISIR'],
      ['SOG massima', val(sog.value, 2) + ' kn', `VISIR: ${sog.count}/${points.length} campioni validi`],
      ['Vento massimo', val(wind.value, 2) + ' kn', `VISIR: ${wind.count}/${points.length} campioni validi`],
      ['Hs massima', val(waves.value, 2) + ' m', `VISIR: ${waves.count}/${points.length} campioni validi`],
    ];
    const propulsion = r.propulsion;
    const propulsionSource = propulsion?.complete ?
      'Flag espliciti VISIR e tempi originali dei segmenti' :
      'Non disponibile: flag o tempi dei segmenti incompleti';
    cards.push(
      ['Ore motore', hasValue(propulsion?.motor_hours) ? val(propulsion.motor_hours, 2) + ' h' : 'Non disponibili', propulsionSource],
      ['Ore vela', hasValue(propulsion?.sail_hours) ? val(propulsion.sail_hours, 2) + ' h' : 'Non disponibili', propulsionSource]
    );
    if (a) {
      cards.push(['Max raffica P90 (punti)', val(gust.value, 2) + ' kn',
        `Nuova analisi ECMWF: ${gust.count}/${points.length} punti; massimo dei P90 puntuali, non P90 di traversata`]);
    }
    $('visir-stats').replaceChildren(...cards.map(([name, value, source]) => {
      const card = document.createElement('div');
      card.className = 'card';
      for (const [text, className] of [[name, 'muted'], [value, 'kpi'], [source, 'small']]) {
        const row = document.createElement('div');
        row.className = className;
        row.textContent = text;
        card.appendChild(row);
      }
      return card;
    }));
  }
  function timeAxis(points) {
    const times = points.filter(p => p.ISO_date).map(p => p.ISO_date);
    const ticks = times.filter((_, j) => j % Math.max(1, Math.ceil(times.length / 5)) === 0);
    return {type: 'date', title: 'Europe/Rome (vedi fuso nei tooltip)',
      gridcolor: '#304357', tickvals: ticks, ticktext: ticks.map(localTime)};
  }
  async function syncZoom(id, ev) {
    if (chartSync || !linkedCharts().includes(id)) return;
    const range = ev['xaxis.range'] || (ev['xaxis.range[0]'] != null ?
      [ev['xaxis.range[0]'], ev['xaxis.range[1]']] : null);
    if (!range && !ev['xaxis.autorange']) return;
    chartSync = true;
    try {
      await Promise.all(linkedCharts().filter(other => other !== id && $(other).data).map(other =>
        Plotly.relayout(other, range ? {'xaxis.range': range, 'xaxis.autorange': false} : {'xaxis.autorange': true})));
    } finally { chartSync = false; }
  }
  function metricOptions(r) {
    for (const id of ['visir-chart-y', 'visir-chart-y2']) {
      const select = $(id);
      const previous = select.options.length ? select.value : id === 'visir-chart-y2' ? 'Hs' : 'wind_magn';
      select.replaceChildren(...(id === 'visir-chart-y2' ? [new Option('Nessuna', '')] : []), ...metrics.map(m => {
        const available = r.waypoints.some((p, i) => hasValue(metricValue(r, i, m)));
        return new Option(`${m.source === 'ecmwf' ? '' : 'VISIR '}${m.label} (${m.unit})${available ? '' : ' - non disponibile'}`, metricKey(m));
      }));
      select.value = previous;
    }
    $('visir-chart-x').querySelector('[value="time"]').disabled = !r.timed;
    if (!r.timed) $('visir-chart-x').value = 'distance';
  }
  async function explorer(r) {
    const chosen = [$('visir-chart-y').value, $('visir-chart-y2').value];
    const series = [...new Set(chosen)].map(field => metrics.find(m => metricKey(m) === field)).filter(Boolean);
    if (!series.length) return;
    const distance = $('visir-chart-x').value === 'distance';
    const available = series.filter(m => r.waypoints.some((p, i) => hasValue(metricValue(r, i, m))));
    const missing = series.filter(m => !available.includes(m));
    const axisAvailable = distance || r.timed;
    const differentUnits = series.length === 2 && series[0].unit !== series[1].unit;
    const message = [
      axisAvailable ? `Asse X: ${distance ? 'distanza cumulata originale (NM)' : 'timestamp originali (Europe/Rome)'}.` :
        'Timestamp assenti o incompleti: scegli le miglia, nessun orario ricostruito.',
      ...series.map((m, i) => available.includes(m) ?
        `${m.label} (${m.unit}): asse ${i === 1 && differentUnits ? 'destro' : 'sinistro'}.` :
        `${m.label}: non disponibile ${m.source === 'ecmwf' ? 'nella nuova analisi ECMWF' : 'nell’export VISIR (non sostituito automaticamente)'}.`),
      'Valori mancanti lasciati vuoti.'
    ].join(' ');
    $('visir-chart-status').textContent = message;
    const l = layout(`${series.map(m => m.label).join(' / ')} - ${r.model} / ${r.optimization}`, series[0].unit);
    const seriesColors = [colorFor(r), colors[(routes.indexOf(r) + 1) % colors.length]];
    l.margin = {...l.margin, r: differentUnits ? 75 : 20, b: 100};
    l.yaxis = {...l.yaxis, title: {text: series[0].unit, font: {color: seriesColors[0]}}};
    if (differentUnits) {
      l.yaxis2 = {title: {text: series[1].unit, font: {color: seriesColors[1]}},
        overlaying: 'y', side: 'right', showgrid: false, zeroline: false};
    }
    l.showlegend = true;
    l.xaxis = distance ? {type: 'linear', title: 'Miglia dalla partenza (NM)', gridcolor: '#304357'} : timeAxis(r.waypoints);
    const canPlot = available.length > 0 && axisAvailable;
    l.annotations = canPlot ? [] : [{
      text: !axisAvailable ? 'Orari assenti: scegli le miglia' :
        missing.map(m => m.label + ': non disponibile').join('<br>'),
      xref: 'paper', yref: 'paper', x: .5, y: .5, showarrow: false
    }];
    const traces = series.flatMap((m, seriesIndex) => {
      if (!axisAvailable || !available.includes(m)) return [];
      const x = [], y = [], customdata = [];
      r.waypoints.forEach((p, i) => {
        const position = distance ? p.cumDist : p.ISO_date;
        const value = metricValue(r, i, m);
        const v = hasValue(value) ? value : null;
        const previous = i ? metricValue(r, i - 1, m) : null;
        if (m.angle && hasValue(previous) && v != null && Math.abs(v - previous) > 180) {
          x.push(position); y.push(null); customdata.push([i, '', '', '']);
        }
        x.push(position); y.push(v);
        customdata.push([i, localTime(p.ISO_date), val(p.cumDist, 3), p.waypoint_ID]);
      });
      return [{
        name: `${m.source === 'ecmwf' ? '' : 'VISIR '}${m.label} (${m.unit})`, yaxis: seriesIndex === 1 && differentUnits ? 'y2' : 'y',
        type: 'scatter', mode: 'lines+markers', x, y, customdata, connectgaps: false,
        line: {color: seriesColors[seriesIndex]}, marker: {size: 5},
        hovertemplate: `Waypoint %{customdata[3]}<br>%{customdata[1]}<br>%{customdata[2]} NM<br>${m.label}: %{y:.2f} ${m.unit}<br>${m.source === 'ecmwf' ? 'Nuova analisi ECMWF run ' + analysisFor(r)?.run_utc : 'Originale VISIR'}<extra></extra>`
      }];
    });
    await Plotly.react('visir-explorer', traces, l, {responsive: true, displaylogo: false});
    const gd = $('visir-explorer');
    for (const event of ['plotly_hover', 'plotly_click', 'plotly_relayout']) gd.removeAllListeners(event);
    const focus = ev => {
      const index = ev.points[0].customdata[0];
      if (selectedId === r.id) mark(r.waypoints[index]);
    };
    gd.on('plotly_hover', focus);
    gd.on('plotly_click', focus);
    gd.on('plotly_relayout', ev => syncZoom('visir-explorer', ev).catch(e => status('Errore sincronizzazione: ' + e.message, true)));
  }
  function analysisInfo(r) {
    const a = analysisFor(r);
    statistics(r);
    $('visir-analyze').disabled = Boolean(STATIC) || !r.timed || analyzing;
    $('visir-analysis-status').textContent = a ?
      `Nuova analisi ECMWF: run ${a.run_utc}; 51 membri richiesti, onde deterministiche. ` +
      `Copertura: raffica ${a.coverage.gust_kn_p50}/${r.waypoint_count}, periodo onda ${a.coverage.wave_period_s}/${r.waypoint_count}, swell ${a.coverage.swell_m}/${r.waypoint_count}. ` +
      a.warnings.join(' ') :
      (r.timed ? 'Nessuna analisi ECMWF salvata. Il pulsante usa solo i GRIB locali del run originale, senza download.' :
        'Analisi ECMWF non possibile: mancano i timestamp originali della rotta.');
    if (analysisDownloadUrl) URL.revokeObjectURL(analysisDownloadUrl);
    const link = $('visir-analysis-download');
    link.hidden = !a;
    if (a) {
      analysisDownloadUrl = URL.createObjectURL(new Blob([JSON.stringify(a)], {type: 'application/json'}));
      link.href = analysisDownloadUrl;
      link.download = `visir-${r.id}-ecmwf-analysis.json`;
    }
  }
  async function loadAnalysis(r) {
    if (!analysisCache.has(r.id)) {
      let a;
      if (STATIC) {
        staticAnalyses ??= fetchJSON(STATIC + 'visir/analysis.json');
        a = (await staticAnalyses)[r.id] ?? null;
      } else {
        a = await fetchJSON('/api/visir/analysis/' + r.id);
      }
      analysisCache.set(r.id, a);
    }
    if (selectedId !== r.id) return;
    analysisInfo(r);
    metricOptions(r);
    await explorer(r);
  }
  async function charts(r) {
    const generation = ++chartGeneration;
    await Promise.all(chartIds.map(async (id, i) => {
      const points = r.waypoints;
      const l = layout(['SOG originale', 'Vento originale', 'Hs originale'][i], units[i]);
      l.margin = {l: 65, r: 20, t: 35, b: 60};
      l.xaxis = timeAxis(points);
      l.annotations = r.timed ? [] : [{
        text: 'Orari assenti o incompleti: nessun tempo ricostruito',
        xref: 'paper', yref: 'paper', x: .5, y: .5, showarrow: false
      }];
      await Plotly.react(id, r.timed ? [{
        type: 'scatter', mode: 'lines+markers', x: points.map(p => p.ISO_date),
        y: points.map(p => p[fields[i]] ?? null), connectgaps: false,
        customdata: points.map(p => [localTime(p.ISO_date), p.waypoint_ID]),
        line: {color: colorFor(r)}, marker: {size: 4},
        hovertemplate: `%{customdata[0]}<br>Waypoint %{customdata[1]}<br>%{y:.2f} ${units[i]}<extra></extra>`
      }] : [], l, {responsive: true, displaylogo: false});
      if (generation !== chartGeneration) return;
      const gd = $(id);
      gd.removeAllListeners('plotly_hover');
      gd.removeAllListeners('plotly_click');
      gd.removeAllListeners('plotly_relayout');
      const focus = ev => {
        const p = r.waypoints[ev.points[0].pointIndex];
        if (selectedId === r.id) mark(p);
      };
      gd.on('plotly_hover', focus);
      gd.on('plotly_click', focus);
      gd.on('plotly_relayout', ev => syncZoom(id, ev).catch(e => status('Errore sincronizzazione: ' + e.message, true)));
    }));
  }
  function showSelected() {
    const r = selectedRoute();
    if (!r) return;
    $('title').textContent = 'Offshore Weather - Rotte VISIR';
    document.title = 'Rotte VISIR | Offshore Weather';
    $('subtitle').textContent = `${r.model} | ${r.optimization === 'time' ? 'Minimo tempo' : 'Minima distanza'} | ${r.run}`;
    $('visir-summary').textContent = `Modello: ${r.model}
Run VISIR: ${r.run} | Ottimizzazione: ${r.optimization} | Waypoint: ${r.waypoint_count}
Partenza: ${localTime(r.departure_utc)}
Arrivo indicativo: ${arrivalTime(r.arrival_utc)}
Durata esportata: ${val(r.duration_hours, 5)} h | Distanza esportata: ${val(r.distance_nm, 4)} NM
Emissione ECMWF da nomi NetCDF: ${r.ecmwf_runs.join(', ') || 'non disponibile'}
Provenienza: ${r.sources.map(s => s.archive + ' [SHA256 ' + s.sha256 + ']').join('; ')}
Membro ZIP: ${r.member}
${r.warnings.map(w => 'ATTENZIONE: ' + w).join('\n')}`;
    $('visir-clock').disabled = !r.timed;
    for (const ext of ['geojson', 'gpx']) {
      const a = $('visir-' + ext);
      a.href = STATIC ? STATIC + 'visir/' + r.id + '.' + ext : '/api/visir/routes/' + r.id + '/' + ext;
      a.download = 'visir-' + r.id + '.' + ext;
    }
    drawMap();
    metricOptions(r);
    explorer(r).catch(e => status('Errore grafico selezionabile: ' + e.message, true));
    charts(r).catch(e => status('Errore grafici VISIR: ' + e.message, true));
    analysisInfo(r);
    loadAnalysis(r).catch(e => status('Errore caricamento analisi ECMWF: ' + e.message, true));
  }
  async function load() {
    routes = await fetchJSON(STATIC ? STATIC + 'visir/routes.json' : '/api/visir/routes');
    selectedId = routes.some(r => r.id === selectedId) ? selectedId :
      (routes.find(r => r.model === 'extra_GS35_Jezequel_cruising_85_hybrid' && r.optimization === 'time') || routes[0])?.id;
    $('visir-select').replaceChildren(...routes.map(r => new Option(label(r), r.id, false, r.id === selectedId)));
    status(routes.length ? `${routes.length} rotte disponibili; una sola rotta visualizzata alla volta.` : 'Nessuna rotta: importa da terminale con python visir.py PERCORSO.zip e ricarica la pagina.');
    if (!routes.length) {
      $('title').textContent = 'Offshore Weather - Rotte VISIR';
      $('subtitle').textContent = 'Importazione delle rotte da terminale';
      for (const id of ['visir-select', 'visir-fit', 'visir-clock', 'visir-analyze', 'visir-chart-x', 'visir-chart-y', 'visir-chart-y2']) $(id).disabled = true;
    }
    showSelected();
    fit();
  }
  $('visir-select').addEventListener('change', () => {
    selectedId = $('visir-select').value;
    showSelected();
    fit();
  });
  $('visir-fit').addEventListener('click', fit);
  $('visir-analyze').addEventListener('click', async () => {
    const r = selectedRoute();
    if (!r?.timed || analyzing) return;
    analyzing = true;
    $('visir-analyze').disabled = true;
    $('visir-analysis-status').textContent = 'Analisi GRIB locali in corso: ensemble completo, nessun download. Attendere...';
    try {
      const response = await fetch('/api/visir/analysis/' + r.id, {method: 'POST'});
      if (!response.ok) throw new Error(await response.text());
      analysisCache.set(r.id, await response.json());
      if (selectedId === r.id) {
        metricOptions(r);
        $('visir-chart-y').value = 'ecmwf:gust_kn_p90';
        $('visir-chart-y2').value = 'ecmwf:wave_period_s';
        await explorer(r);
      }
    } catch (e) {
      status('Analisi ECMWF fallita: ' + e.message, true);
    } finally {
      analyzing = false;
      analysisInfo(selectedRoute());
    }
  });
  for (const id of ['visir-chart-x', 'visir-chart-y', 'visir-chart-y2']) {
    $(id).addEventListener('change', () => {
      const r = selectedRoute();
      if (r) explorer(r).catch(e => status('Errore grafico selezionabile: ' + e.message, true));
    });
  }
  $('visir-time-charts').addEventListener('toggle', () => {
    if ($('visir-time-charts').open) chartIds.forEach(id => { if ($(id).data) Plotly.Plots.resize($(id)); });
  });
  $('visir-clock').addEventListener('input', () => {
    const r = selectedRoute();
    if (!r?.timed) return;
    const pts = r.waypoints, first = Date.parse(pts[0].ISO_date), last = Date.parse(pts.at(-1).ISO_date);
    const target = first + (last - first) * Number($('visir-clock').value) / 1000;
    const right = pts.findIndex(p => Date.parse(p.ISO_date) >= target);
    if (right <= 0 || Date.parse(pts[right].ISO_date) === target) { mark(pts[Math.max(0, right)]); return; }
    const a = pts[right - 1], b = pts[right];
    const f = (target - Date.parse(a.ISO_date)) / (Date.parse(b.ISO_date) - Date.parse(a.ISO_date));
    // Interpolate position/cumulatives only. Weather and angles are never fabricated.
    const p = {ISO_date: new Date(target).toISOString()};
    for (const key of ['lat', 'cumDist', 'cumTime']) {
      p[key] = a[key] == null || b[key] == null ? null : a[key] + f * (b[key] - a[key]);
    }
    const deltaLon = ((b.lon - a.lon + 540) % 360) - 180;
    p.lon = ((a.lon + f * deltaLon + 540) % 360) - 180;
    mark(p, true);
  });
  $('visir-map-slot').appendChild($('map'));
  map.removeLayer(forecastLayer);
  overlay.addTo(map);
  map.invalidateSize({animate: false});
  load().catch(e => status('Caricamento VISIR fallito: ' + e.message, true));
})();
