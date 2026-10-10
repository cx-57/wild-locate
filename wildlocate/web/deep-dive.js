'use strict';

// Investigating roads, buildings, and terrain

function initDeepDiveMap() {
  if (deepDiveMap || !window.L) {
    if (!window.L) {
      $('deep-dive-map-status').hidden = false;
      $('deep-dive-map-status').textContent = 'Map unavailable — the report is still available below';
    }
    return;
  }
  const target = deepDiveResult || result;
  const latitude = Number(target?.latitude);
  const longitude = Number(target?.longitude);
  deepDiveMap = L.map('deep-dive-map', {scrollWheelZoom: true, touchZoom: true, wheelPxPerZoomLevel: 80, zoomSnap: .25, minZoom: 3, maxZoom: 18})
    .setView(Number.isFinite(latitude) && Number.isFinite(longitude) ? [latitude, longitude] : [39, -96], 9);
  deepDiveOverlay = L.layerGroup().addTo(deepDiveMap);
  const tiles = L.tileLayer(
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {
      maxZoom: 18,
      noWrap: true,
      keepBuffer: 0,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }
  );
  tiles.on('tileerror', () => {
    $('deep-dive-map-status').hidden = false;
    $('deep-dive-map-status').textContent = 'Map imagery could not load. Mapped feature overlays and the place findings can still be inspected; check your internet connection';
  });
  tiles.on('load', () => {
    $('deep-dive-map-status').hidden = true;
  });
  tiles.addTo(deepDiveMap);
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => deepDiveMap?.invalidateSize({pan: false})).observe($('deep-dive-map'));
  }
}

function previewDeepDiveArea(request) {
  initDeepDiveMap();
  if (!deepDiveMap || !deepDiveOverlay) return;
  deepDiveOverlay.clearLayers();
  deepDiveSampleMarkers.clear();
  deepDivePlaceLayers.clear();
  $('deep-dive-places-map-key').hidden = true;
  const center = [Number(request.latitude), Number(request.longitude)];
  if (!center.every(Number.isFinite)) return;
  deepDiveBounds = L.circle(center, {
    radius: Math.max(1, Number(request.radius_km) || 10) * 1000,
    color: '#315b48', weight: 1.4, fillOpacity: .015,
    interactive: false,
  }).addTo(deepDiveOverlay).getBounds();
  deepDiveMap.invalidateSize({pan: false});
  deepDiveMap.fitBounds(deepDiveBounds, {padding: [26, 26], animate: false});
}

function drawDeepDiveMap(report) {
  initDeepDiveMap();
  if (!deepDiveMap || !deepDiveOverlay) return;
  deepDiveOverlay.clearLayers();
  deepDiveSampleMarkers.clear();
  deepDivePlaceLayers.clear();
  const center = [report.latitude, report.longitude];
  const circle = L.circle(center, {
    radius: report.radius_km * 1000,
    color: '#315b48',
    weight: 1.3,
    fillOpacity: 0.018,
    interactive: false,
  }).addTo(deepDiveOverlay);
  deepDiveBounds = circle.getBounds();

  // Keep mapped places visible instead of covering them with sample markers
  drawDeepDivePlaces(report);
  // Let Leaflet measure the visible map before fitting bounds
  deepDiveMap.invalidateSize({pan: false});
  deepDiveMap.fitBounds(deepDiveBounds, {padding: [26, 26], animate: false});
  setTimeout(() => {
    if (!deepDiveMap || $('deep-dive-page').hidden) return;
    deepDiveMap.invalidateSize({pan: false});
    deepDiveMap.fitBounds(deepDiveBounds, {padding: [26, 26], animate: false});
  }, 0);
}

function deepDiveScopeText(source, radius) {
  if (source?.analysis_type === 'regional') {
    return `Regional analysis · Deep Dive examines specific places within your original ${radius} km radius.`;
  }
  if (source) {
    return `Started from one point · Deep Dive examines the surrounding ${radius} km radius, not just that coordinate. Locations in the region are sampled and mapped features are investigated individually.`;
  }
  return `Deep Dive examines a ${radius} km radius around the selected coordinate rather than a single point.`;
}

function renderDeepDive(report) {
  deepDiveResult = report;
  $('deep-dive-loading').hidden = true;
  $('deep-dive-error').hidden = true;
  $('deep-dive-retry').hidden = true;
  $('deep-dive-export').disabled = false;
  $('deep-dive-print').disabled = false;
  $('deep-dive-content').hidden = false;
  $('deep-dive-title').textContent = `${report.species} · specific places`;
  $('deep-dive-meta').textContent = `${report.radius_km} km radius · ${report.model} · ${coords(report.latitude, report.longitude)}`;
  $('deep-dive-scope-note').textContent = deepDiveScopeText(deepDiveSource, report.radius_km);

  const overview = report.overview || {};
  const evaluated = overview.evaluated_points ?? 0;
  const total = overview.total_points ?? report.sample_points ?? evaluated;
  const mapped = report.places?.mapped_features ?? 0;
  const terrain = report.places?.terrain_samples ?? 0;
  $('deep-dive-summary').textContent = mapped || terrain
    ? `Investigate ${mapped} mapped features and ${terrain} terrain samples around your location. Select a place below to examine the model evidence and local environmental context.`
    : 'Mapped structures and usable terrain samples could not be confirmed here. This does not mean roads, buildings or habitat features are absent.';
  $('deep-dive-coverage-note').textContent =
    `${evaluated} of ${total} habitat samples had usable environmental data. ` +
    'Mapped features are searched only within selected neighborhoods; neither the map nor the models provide complete coverage of the study area.';

  renderDeepDivePlaces(report);
  renderDeepDiveEvidence(report);
  drawDeepDiveMap(report);
}

function validPlaceLocation(site) {
  return site && Number.isFinite(site.latitude) && Math.abs(site.latitude) <= 90 &&
    Number.isFinite(site.longitude) && Math.abs(site.longitude) <= 180;
}

function placeLabel(site) {
  if (site.kind === 'habitat') return site.terrain_sample ? 'Terrain' : 'Habitat area';
  return ({road: 'Road', building: 'Building'})[site.kind] || 'Place';
}

function placeSourceLink(parent, label, value) {
  if (typeof value !== 'string') return;
  try {
    const url = new URL(value);
    if (!['https:', 'http:'].includes(url.protocol)) return;
    const link = document.createElement('a');
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = label;
    parent.append(link);
  } catch (_) { /* Missing or invalid source URLs remain plain text */ }
}

function renderDeepDivePlaces(report) {
  const places = report.places;
  const parent = $('deep-dive-places-list');
  const evidence = $('deep-dive-places-evidence');
  parent.replaceChildren();
  evidence.replaceChildren();
  const sites = Array.isArray(places?.sites) ? places.sites.filter(validPlaceLocation).slice(0, 9) : [];
  const status = places?.status;
  $('deep-dive-places-status').textContent = !places ? 'Earlier report'
    : ({available: 'Mapped places', partial: 'Partial analysis', unavailable: 'Mapping unavailable', no_signal: 'No mapped locations'})[status] || 'Mapping incomplete';
  $('deep-dive-places-summary').textContent = !places
    ? 'This report contains no specific-place analysis — run a new Deep Dive to investigate individual mapped roads, buildings, and habitat areas'
    : status === 'unavailable' && !sites.length
    ? 'Mapped places could not be evaluated, so no conclusion can be drawn about individual roads or buildings'
    : status === 'no_signal' && !sites.length
    ? 'No supported mapped features were retrieved in the inspected neighborhoods. Incomplete coverage cannot establish their absence'
    : places.summary || 'Named places with surrounding conditions worth checking in the field';
  const areas = Array.isArray(places?.searched_areas) ? places.searched_areas.filter(validPlaceLocation) : [];
  const radii = [...new Set(areas.filter(area => Number.isFinite(area.radius_m)).map(area => area.radius_m / 1000))];
  const size = radii.length === 1 ? `${radii[0]} km radius ` : '';
  $('deep-dive-places-coverage').textContent = places
    ? `${areas.length} selected ${size}neighborhood${areas.length === 1 ? '' : 's'} for search · ${places.mapped_features ?? 0} mapped features · ${places.terrain_samples ?? 0} terrain samples · ${places.assessed_features ?? 0} evaluated` +
      (places.failed_evaluations ? ` · ${places.failed_evaluations} evaluations failed` : '') +
      ' · This is a partial map search, not an inventory of all places in the area'
    : '';
  $('deep-dive-places-map-key').hidden = !sites.length;
  sites.forEach((site, index) => {
    const rank = index + 1;
    const card = document.createElement('article');
    card.className = 'place-card';
    const head = document.createElement('div');
    head.className = 'place-card-heading';
    const focus = document.createElement('button');
    focus.type = 'button';
    focus.className = 'place-focus';
    focus.setAttribute('aria-label', `Show P${rank}: ${site.name || placeLabel(site)} on the map`);
    const badge = document.createElement('span');
    badge.className = 'place-rank';
    badge.textContent = `P${rank}`;
    const title = document.createElement('strong');
    title.textContent = site.name || `Mapped ${placeLabel(site).toLowerCase()}`;
    focus.append(badge, title);
    focus.addEventListener('click', () => focusDeepDivePlace(site));
    const kind = document.createElement('span');
    kind.className = `place-type ${['road', 'building', 'habitat'].includes(site.kind) ? site.kind : ''}`;
    kind.textContent = placeLabel(site);
    head.append(focus, kind);
    const signals = (Array.isArray(site.signals) ? site.signals : []).filter(signal =>
      signal && typeof signal.feature === 'string' && Number.isFinite(signal.effect) && signal.effect < 0 &&
      Number.isFinite(signal.current) && Number.isFinite(signal.reference));
    const supporting = (Array.isArray(site.supporting_signals) ? site.supporting_signals : []).filter(signal =>
      signal && typeof signal.feature === 'string' && Number.isFinite(signal.effect) && signal.effect > 0 &&
      Number.isFinite(signal.current) && Number.isFinite(signal.reference));
    const strongest = [...signals].sort((a, b) => a.effect - b.effect)[0];
    const positive = [...supporting].sort((a, b) => b.effect - a.effect)[0];
    const comparison = document.createElement('p');
    comparison.className = 'place-comparison';
    comparison.textContent = strongest
      ? `−${(-strongest.effect * 100).toFixed(1)} model-score points · limiting local condition`
      : positive
      ? `+${(positive.effect * 100).toFixed(1)} model-score points · supporting local condition`
      : site.assessment_status === 'unavailable'
      ? `${site.terrain_sample ? 'Terrain sample' : 'Mapped feature'} · local model assessment unavailable`
      : `${site.terrain_sample ? 'Terrain sample' : 'Mapped feature'} · no substantial model limitation identified`;
    const caption = document.createElement('p');
    caption.className = 'place-caption';
    caption.textContent = strongest ? friendlyFeature(strongest.feature)
      : positive ? friendlyFeature(positive.feature)
      : (site.assessment_note || 'No measured model effect can be attributed to this individual feature');
    if (Number.isFinite(site.percentile)) caption.textContent += ` · ${ordinal(site.percentile)} habitat percentile`;
    const details = document.createElement('details');
    details.className = 'place-evidence';
    const summary = document.createElement('summary');
    summary.textContent = 'Evidence and field checks';
    const body = document.createElement('div');
    body.className = 'place-evidence-body';
    const sizeScope = site.size_scope === 'within_inspected_area' ? ' within inspected area' : '';
    if (Number.isFinite(site.length_m)) researchRow(body, `Mapped road length${sizeScope}`, `${Math.round(site.length_m).toLocaleString()} m`);
    if (Number.isFinite(site.area_m2)) researchRow(body, `Mapped footprint${sizeScope}`, `${Math.round(site.area_m2).toLocaleString()} m²`);
    for (const signal of signals) researchRow(body, friendlyFeature(signal.feature),
      `Here: ${featureValue(signal.feature, signal.current)} · typical: ${featureValue(signal.feature, signal.reference)} · limiting model comparison: −${(-signal.effect * 100).toFixed(1)} score points`);
    for (const signal of supporting) researchRow(body, friendlyFeature(signal.feature),
      `Here: ${featureValue(signal.feature, signal.current)} · typical: ${featureValue(signal.feature, signal.reference)} · supporting model comparison: +${(signal.effect * 100).toFixed(1)} score points`);
    if (typeof site.assessment_note === 'string') researchRow(body, 'Interpretation', site.assessment_note);
    if (site.nearby_habitat && Number.isFinite(site.nearby_habitat.distance_m) && Number.isFinite(site.nearby_habitat.percentile)) {
      researchRow(body, 'Nearby habitat sample', `${ordinal(site.nearby_habitat.percentile)} percentile · ${Math.round(site.nearby_habitat.distance_m).toLocaleString()} m away, without confirming habitat between the locations`);
    }
    const pathways = (Array.isArray(site.potential_pathways) ? site.potential_pathways : [])
      .filter(value => typeof value === 'string');
    if (pathways.length) researchRow(body, 'Possible environmental interactions to verify (not measured)',
      pathways.join(' · '));
    const checks = document.createElement('ul');
    checks.className = 'place-checks';
    for (const check of Array.isArray(site.field_checks) ? site.field_checks : []) {
      if (typeof check !== 'string') continue;
      const item = document.createElement('li');
      item.textContent = uiPhrase(check);
      checks.append(item);
    }
    if (checks.children.length) body.append(checks);
    const location = document.createElement('p');
    location.className = 'place-location';
    location.textContent = coords(site.latitude, site.longitude);
    body.append(location);
    placeSourceLink(body, `View source${site.source ? ` in ${site.source}` : ''} ↗`, site.source_url);
    details.append(summary, body);
    card.append(head, comparison, caption, details);
    parent.append(card);
  });
  const paragraphs = places
    ? [places.method, places.limitations, ...(Array.isArray(places.warnings) ? places.warnings : [])]
    : ['Run a new report to retrieve real mapped features and compare the habitat conditions around selected locations'];
  for (const value of paragraphs) {
    if (typeof value !== 'string' || !value) continue;
    const p = document.createElement('p');
    p.textContent = value;
    evidence.append(p);
  }
  const note = document.createElement('p');
  note.textContent = 'Rankings prioritize the strongest limiting nearby model association; other mapped places are retained even with neutral or missing model results. These comparisons cannot prove that a named road or building caused environmental harm.';
  evidence.append(note);
  const sources = document.createElement('div');
  sources.className = 'place-sources';
  for (const source of Array.isArray(places?.sources) ? places.sources : []) {
    if (!source || typeof source !== 'object') continue;
    const row = document.createElement('div');
    row.className = 'place-source';
    placeSourceLink(row, source.name || 'Mapping source', source.url);
    const metadata = [source.attribution, source.data_timestamp ? `Map data timestamp: ${source.data_timestamp}` : '', source.coverage]
      .filter(value => typeof value === 'string' && value);
    if (metadata.length) {
      const p = document.createElement('p');
      p.textContent = metadata.join(' · ');
      row.append(p);
    }
    if (row.children.length) sources.append(row);
  }
  if (sources.children.length) evidence.append(sources);
  if (typeof places?.retrieved_at === 'string') {
    const retrieved = new Date(places.retrieved_at);
    if (Number.isFinite(retrieved.getTime())) {
      const p = document.createElement('p');
      p.textContent = `Mapping retrieved ${retrieved.toLocaleString()}`;
      evidence.append(p);
    }
  }
}

function validPlaceGeometry(geometry) {
  const depths = {Point: 0, LineString: 1, MultiLineString: 2, Polygon: 2, MultiPolygon: 3};
  if (!geometry || !Object.hasOwn(depths, geometry.type)) return false;
  let count = 0;
  const check = (values, depth) => Array.isArray(values) && values.length > 0 && (depth
    ? values.every(value => check(value, depth - 1))
    : ++count <= 3000 && values.length >= 2 && Number.isFinite(values[0]) && Math.abs(values[0]) <= 180 && Number.isFinite(values[1]) && Math.abs(values[1]) <= 90);
  return check(geometry.coordinates, depths[geometry.type]);
}

function drawDeepDivePlaces(report) {
  const sites = Array.isArray(report.places?.sites) ? report.places.sites.filter(validPlaceLocation).slice(0, 9) : [];
  sites.forEach((site, index) => {
    const rank = index + 1;
    const color = ({road: '#8a6846', building: '#786780', habitat: '#637e59'})[site.kind] || '#786b59';
    const popup = document.createElement('div');
    const name = site.name || placeLabel(site);
    popup.textContent = `P${rank} · ${name}${Number.isFinite(site.percentile) ? ` · ${ordinal(site.percentile)} habitat percentile` : ''}`;
    let geometry = null;
    if (typeof L.geoJSON === 'function' && site.geometry?.type !== 'Point' && validPlaceGeometry(site.geometry)) {
      try {
        geometry = L.geoJSON(site.geometry, {style: {color, weight: site.kind === 'road' ? 3 : 2, opacity: .82, fillColor: color, fillOpacity: .16}})
          .bindPopup(popup.cloneNode(true)).addTo(deepDiveOverlay);
      } catch (_) { /* A malformed footprint must not hide the report */ }
    }
    const marker = L.marker([site.latitude, site.longitude], {
      icon: L.divIcon({className: 'place-rank-marker', html: `P${rank}`, iconSize: [32, 26], iconAnchor: [16, 13]}),
      title: `Place P${rank}: ${name}`,
    }).bindPopup(popup).addTo(deepDiveOverlay);
    deepDivePlaceLayers.set(site.id, {geometry, marker});
  });
}

function focusDeepDivePlace(site) {
  if (!deepDiveMap) return;
  const layers = deepDivePlaceLayers.get(site.id);
  const bounds = layers?.geometry?.getBounds?.();
  if (bounds?.isValid?.()) deepDiveMap.fitBounds(bounds, {padding: [42, 42], maxZoom: 16, animate: false});
  else deepDiveMap.setView([site.latitude, site.longitude], 16, {animate: false});
  layers?.marker?.openPopup();
  $('deep-dive-map').scrollIntoView({behavior: scrollBehavior(), block: 'center'});
}

function renderDeepDiveEvidence(report) {
  const parent = $('deep-dive-evidence-stats');
  parent.replaceChildren();
  const e = report.evidence;
  if (!e) {
    $('deep-dive-evidence-copy').textContent = 'Validation metadata is unavailable in this saved report, and its percentiles compare the fitted model’s reference locations rather than estimate confidence.';
    $('deep-dive-evidence-sources').textContent = '';
    return;
  }
  parent.append(reviewStat('Recorded sightings', Number(e.presence_count).toLocaleString()),
    reviewStat('Background locations', Number(e.background_count).toLocaleString()), validationHelp(e, report.model));
  $('deep-dive-evidence-copy').textContent = 'The model learns from sightings and environmental data, with performance checked on locations left out of training.';
  $('deep-dive-evidence-sources').textContent = (e.sources || []).join(' · ');
}

function setDeepDiveBusy(value) {
  deepDiveBusy = value;
  $('deep-dive-loading').hidden = !value;
  $('deep-dive-cancel').disabled = !value || !deepDiveJobId;
  $('train-species-nav').disabled = value || busy;
  $('models-nav').disabled = value || busy;
  $('analyze').disabled = value || busy || !$('species').value;
  $('deep-dive-launch').disabled = value;
  $('deep-dive-export').disabled = value || !deepDiveResult;
  $('deep-dive-print').disabled = value || !deepDiveResult;
  if (deepDiveStageTimer) clearInterval(deepDiveStageTimer);
  deepDiveStageTimer = null;
  if (value) {
    deepDiveStarted = Date.now();
    const updateElapsed = () => {
      const seconds = Math.floor((Date.now() - deepDiveStarted) / 1000);
      const elapsed = seconds < 60 ? `${seconds}s elapsed` : `${Math.floor(seconds / 60)}m ${seconds % 60}s elapsed`;
      $('deep-dive-loading-stage').textContent = `Checking habitat across this area · ${elapsed}`;
    };
    updateElapsed();
    deepDiveStageTimer = setInterval(updateElapsed, 1000);
  }
}

function deepDiveError(message) {
  setDeepDiveBusy(false);
  $('deep-dive-loading').hidden = true;
  $('deep-dive-error').textContent = uiPhrase(message);
  $('deep-dive-error').hidden = false;
  $('deep-dive-retry').hidden = false;
}

async function pollDeepDive(id, version) {
  if (deepDiveJobId !== id || deepDiveRevision !== version) return;
  try {
    const job = await api(`/api/deep-dives/${id}`);
    if (deepDiveJobId !== id || deepDiveRevision !== version) return;
    if (job.status === 'running') {
      setTimeout(() => pollDeepDive(id, version), 700);
      return;
    }
    setDeepDiveBusy(false);
    if (job.status === 'complete') {
      renderDeepDive(job.result);
    } else if (job.status === 'cancelled') {
      deepDiveError('Deep Dive cancelled — your habitat assessment is still available');
    } else {
      deepDiveError(job.error || 'Check your model and environmental datasets, then retry the Deep Dive');
    }
  } catch (exc) {
    if (deepDiveJobId !== id || deepDiveRevision !== version) return;
    if (exc.status && exc.status < 500) {
      deepDiveError(exc.message);
      return;
    }
    $('deep-dive-loading-stage').textContent = 'Connection interrupted — retrying the Deep Dive…';
    setTimeout(() => pollDeepDive(id, version), 2000);
  }
}

async function startDeepDive() {
  if (!result || deepDiveBusy) return;
  if (deepDiveResult && deepDiveSource === result) {
    showSection('deep-dive');
    renderDeepDive(deepDiveResult);
    return;
  }
  const radius = result.analysis_type === 'regional' ? result.radius_km : 10;
  const request = {
    species: result.species,
    region: result.region || $('region').value,
    latitude: result.latitude,
    longitude: result.longitude,
    radius_km: radius,
  };

  deepDiveRevision += 1;
  const version = deepDiveRevision;
  deepDiveJobId = null;
  deepDiveResult = null;
  deepDiveSource = result;
  $('deep-dive-content').hidden = true;
  $('deep-dive-error').hidden = true;
  $('deep-dive-retry').hidden = true;
  $('deep-dive-title').textContent = `${result.species} · specific places`;
  $('deep-dive-meta').textContent =
    `${radius} km region · ${result.analysis_type === 'regional' ? 'area' : 'point'} origin`;
  $('deep-dive-scope-note').textContent = deepDiveScopeText(result, radius);
  $('deep-dive-summary').textContent = 'Checking habitat across this area';
  $('deep-dive-demo-note').hidden = !result.recorded_at;
  $('deep-dive-demo-note').textContent = result.recorded_at
    ? 'The example assessment was recorded, while this Deep Dive runs a new live analysis using your local environmental data and current model' : '';
  showSection('deep-dive');
  setDeepDiveBusy(true);
  // Show the selected area while the model analyzes it
  setTimeout(() => {
    if (deepDiveRevision === version && !deepDiveResult && !$('deep-dive-page').hidden) previewDeepDiveArea(request);
  }, 0);

  try {
    const job = await api('/api/deep-dives', request);
    if (deepDiveRevision !== version) return;
    deepDiveJobId = job.id;
    $('deep-dive-cancel').disabled = false;
    pollDeepDive(job.id, version);
  } catch (exc) {
    if (deepDiveRevision !== version) return;
    deepDiveError(exc.message);
  }
}

async function cancelDeepDive() {
  if (!deepDiveJobId || !deepDiveBusy) return;
  const id = deepDiveJobId;
  const version = ++deepDiveRevision;
  $('deep-dive-cancel').disabled = true;
  try {
    await api(`/api/deep-dives/${id}/cancel`, {});
    if (deepDiveJobId !== id || deepDiveRevision !== version) return;
    deepDiveJobId = null;
    deepDiveError('Deep Dive cancelled — your habitat assessment is still available');
  } catch (exc) {
    if (deepDiveJobId !== id || deepDiveRevision !== version) return;
    $('deep-dive-error').textContent = uiPhrase(`Could not cancel: ${exc.message}`);
    $('deep-dive-error').hidden = false;
    $('deep-dive-cancel').disabled = false;
    pollDeepDive(id, version);
  }
}

$('deep-dive-launch').addEventListener('click', startDeepDive);
$('deep-dive-back').addEventListener('click', () => showSection('explore'));
$('deep-dive-cancel').addEventListener('click', cancelDeepDive);
$('deep-dive-retry').addEventListener('click', startDeepDive);
$('deep-dive-print').addEventListener('click', () => deepDiveResult && printCurrentReport('deep-dive-content', 'deep-dive-print'));

