'use strict';

const $ = id => document.getElementById(id);
const uiPhrase = value => String(value ?? '').trim().replace(/([^.])\.$/, '$1');

let config = null;
let token = null;
let regional = false;
let busy = false;
let activeJob = null;
let result = null;
let revision = 0;
let started = 0;
let elapsedTimer = null;
let map = null;
let overlay = null;
const sampleMarkers = new Map();
let authCreate = false;

let deepDiveJobId = null;
let deepDiveRevision = 0;
let deepDiveBusy = false;
let deepDiveMap = null;
let deepDiveOverlay = null;
let deepDiveBounds = null;
let printMapViews = null;
let deepDiveStageTimer = null;
let deepDiveStarted = 0;
let deepDiveResult = null;
let deepDiveSource = null;
const deepDiveSampleMarkers = new Map();
const deepDivePlaceLayers = new Map();

let managerRegion = 'MA';
let modelRecords = [];
let selectedModelId = null;
let trainingJobId = null;
let trainingState = null;
let trainingRevision = 0;
let resolvedTaxon = null;
let handledCompletionId = null;

const colors = ['#b5423a', '#d88735', '#d5bb45', '#80a952', '#286648'];
const number = value => Number.isFinite(value) ? value.toFixed(3) : '—';
const ordinal = value => {
  const n = Math.round(Number(value) || 0);
  const mod100 = n % 100;
  const suffix = mod100 >= 11 && mod100 <= 13
    ? 'th'
    : ({1:'st',2:'nd',3:'rd'}[n % 10] || 'th');
  return `${n}${suffix}`;
};
const coords = (lat, lon) =>
  `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? 'N' : 'S'} / ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? 'E' : 'W'}`;
const scrollBehavior = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';

function error(message) {
  $('error').textContent = uiPhrase(message);
  $('error').hidden = !message;
}

function authError(message) {
  $('auth-error').textContent = uiPhrase(message);
  $('auth-error').hidden = !message;
}

async function api(path, payload, method = payload === undefined ? 'GET' : 'POST') {
  const options = {method};
  if (method !== 'GET') {
    options.headers = {
      'Content-Type': 'application/json',
      'X-Wildlocate-Token': token || '',
    };
    options.body = JSON.stringify(payload || {});
  }
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch (_) {
    data = {};
  }
  if (!response.ok) {
    const failure = Error(data.error || 'Could not contact the local server');
    failure.status = response.status;
    throw failure;
  }
  return data;
}

function showAuth() {
  $('app-shell').hidden = true;
  $('auth-screen').hidden = false;
  $('auth-password').value = '';
  $('auth-confirm').value = '';
  authError('');
  setTimeout(() => $('auth-username').focus(), 0);
}

function setAuthMode(create) {
  authCreate = create;
  $('auth-title').textContent = create ? 'Create your account.' : 'Welcome back.';
  $('auth-copy').textContent = create
    ? 'Save the species models you train on this computer'
    : 'Sign in to train species and keep your saved models';
  $('confirm-wrap').hidden = !create;
  $('auth-confirm').required = create;
  $('auth-submit').firstChild.textContent = create ? 'Create account ' : 'Sign in ';
  $('auth-switch').textContent = create
    ? 'Already have an account? Sign in'
    : 'New here? Create an account';
  $('auth-password').value = '';
  $('auth-confirm').value = '';
  authError('');
}

$('auth-switch').addEventListener('click', () => setAuthMode(!authCreate));
$('auth-back').addEventListener('click', () => enterApp(false));

$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  if (authCreate && password !== $('auth-confirm').value) {
    authError('Your passwords don’t match');
    return;
  }
  $('auth-submit').disabled = true;
  authError('');
  try {
    const data = await api('/api/auth/login', {
      username,
      password,
      create: authCreate,
    });
    config = data;
    token = data.token;
    enterApp(false);
  } catch (exc) {
    authError(exc.message);
  } finally {
    $('auth-submit').disabled = false;
  }
});

$('sign-out').addEventListener('click', async () => {
  if (!config?.authenticated) {
    setAuthMode(false);
    showAuth();
    return;
  }
  try {
    const data = await api('/api/auth/logout', {});
    config = data;
    token = data.token;
  } catch (_) {
    // The server may already have reset. Either way return to the sign-in screen.
  }
  activeJob = null;
  result = null;
  deepDiveJobId = null;
  deepDiveRevision += 1;
  deepDiveResult = null;
  deepDiveSource = null;
  deepDiveBusy = false;
  if (deepDiveStageTimer) clearInterval(deepDiveStageTimer);
  deepDiveStageTimer = null;
  $('species-page').hidden = true;
  $('deep-dive-page').hidden = true;
  $('explore-page').hidden = false;
  setAuthMode(false);
  enterApp(false);
});

function initMap() {
  if (map || !window.L) {
    if (!window.L) {
      $('map-status').hidden = false;
      $('map-status').textContent = 'Map unavailable — enter coordinates manually';
      document.querySelector('.manual').open = true;
    }
    return;
  }

  map = L.map('map', {scrollWheelZoom: true, touchZoom: true, wheelPxPerZoomLevel: 80, zoomSnap: .25, minZoom: 3, maxZoom: 18})
    .setView([42.37, -72.28], 9);
  overlay = L.layerGroup().addTo(map);

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
    $('map-status').hidden = false;
    $('map-status').textContent = 'Map tiles are temporarily unavailable — enter coordinates manually';
  });
  tiles.on('load', () => {
    $('map-status').hidden = true;
  });
  tiles.addTo(map);

  map.on('click', event => {
    if (busy) return;
    $('latitude').value = event.latlng.lat.toFixed(6);
    $('longitude').value = event.latlng.wrap().lng.toFixed(6);
    changed();
  });

  new ResizeObserver(() => map.invalidateSize({pan: false})).observe($('map'));
}

function enterApp(preserveSelection = false) {
  if (!config) {
    showAuth();
    return;
  }
  $('auth-screen').hidden = true;
  $('app-shell').hidden = false;
  $('account-name').textContent = config.username || 'Local exploration';
  $('sign-out').textContent = config.authenticated ? 'Sign out' : 'Sign in';
  showSection('explore');
  initMap();
  applyConfig(config, preserveSelection);
  setTimeout(() => map && map.invalidateSize({pan: false}), 0);
}

function applyConfig(data, preserveSelection = true) {
  const previousRegion = preserveSelection ? $('region').value : '';
  const previousSpecies = preserveSelection ? $('species').value : '';
  config = data;
  token = data.token;
  $('account-name').textContent = data.username || 'Local exploration';

  $('region').replaceChildren(
    ...data.regions.map(state => {
      const option = new Option(state.species.length ? state.name : `${state.name} · train a model first`, state.code);
      option.disabled = !state.species.length;
      return option;
    })
  );
  $('species-region').replaceChildren(
    ...data.regions.map(state => new Option(state.name, state.code))
  );
  if (previousRegion && data.regions.some(state => state.code === previousRegion)) {
    $('region').value = previousRegion;
  } else if (data.regions.some(state => state.code === 'MA')) {
    $('region').value = 'MA';
  }
  if (!managerRegion || !data.regions.some(state => state.code === managerRegion)) {
    managerRegion = $('region').value || 'MA';
  }
  $('species-region').value = managerRegion;
  $('inputs').disabled = false;
  changeRegion(!preserveSelection, previousSpecies);
}

async function refreshConfig(preserveSelection = true) {
  const data = await api('/api/config');
  applyConfig(data, preserveSelection);
}

function selection() {
  if (!$('latitude').value.trim() || !$('longitude').value.trim()) {
    throw Error('Enter both latitude and longitude');
  }
  const latitude = Number($('latitude').value);
  const longitude = Number($('longitude').value);
  if (
    !Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
    !Number.isFinite(longitude) || Math.abs(longitude) > 180
  ) {
    throw Error('Latitude must be −90 to 90; longitude must be −180 to 180');
  }
  if (!$('species').value) {
    throw Error('Train or enable a model to explore this state');
  }
  return {
    species: $('species').value,
    region: $('region').value,
    latitude,
    longitude,
    ...(regional ? {radius_km: Number($('radius').value)} : {}),
  };
}

function setBusy(value) {
  busy = value;
  document.body.classList.toggle('busy', value);
  $('inputs').disabled = value || !config;
  $('train-species-nav').disabled = value;
  $('models-nav').disabled = value;
  $('try-example').disabled = value;
  $('sign-out').disabled = value;
  $('analyze').disabled = value || !$('species').value;
  $('cancel').hidden = !value;
  $('cancel').disabled = !activeJob;

  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
  if (value) {
    started = Date.now();
    const update = () => {
      $('status').textContent =
        `Analyzing habitat · ${Math.floor((Date.now() - started) / 1000)}s`;
    };
    update();
    elapsedTimer = setInterval(update, 1000);
  }
}

function clearResult() {
  revision += 1;
  result = null;
  $('result').hidden = true;
  $('empty').hidden = false;
  $('scores').open = false;
  $('conditions').open = false;
  $('point-percentile').hidden = true;
  error('');
}

function draw(recenter = false) {
  const lat = Number($('latitude').value);
  const lon = Number($('longitude').value);
  const valid =
    $('latitude').value.trim() &&
    $('longitude').value.trim() &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180;

  $('selected-location').textContent = valid ? coords(lat, lon) : 'Enter valid coordinates';
  if (!map) return;
  overlay.clearLayers();
  sampleMarkers.clear();
  if (!valid || Math.abs(lat) > 85) return;

  const center = [lat, lon];
  if (regional) {
    const circle = L.circle(center, {
      radius: Number($('radius').value) * 1000,
      color: '#315b48',
      weight: 1.4,
      fillOpacity: 0.025,
      interactive: false,
    }).addTo(overlay);
    if (recenter) {
      map.fitBounds(circle.getBounds(), {padding: [24, 24], animate: false});
    }
  } else if (recenter) {
    map.setView(center, 11, {animate: false});
  }

  const points = result ? (result.points || [{...result, status: 'ok'}]) : [];
  if (!points.length) {
    L.marker(center, {
      icon: L.divIcon({
        className: 'center-pin',
        iconSize: [15, 15],
        iconAnchor: [7.5, 7.5],
      }),
    }).addTo(overlay);
  }

  for (const point of points) {
    const ok = point.status === 'ok';
    const percentile = Number(point.percentile);
    const color = ok && Number.isFinite(percentile)
      ? colors[Math.min(4, Math.max(0, Math.floor(percentile / 20)))]
      : '#858585';
    const popup = document.createElement('div');
    popup.textContent =
      `${coords(point.latitude, point.longitude)} — ` +
      (ok
        ? `${point.category} · score ${number(point.score)} · percentile ${point.percentile}`
        : 'Unavailable: outside coverage or incomplete data');
    const rank = surveySamples(result).findIndex(p => p.latitude === point.latitude && p.longitude === point.longitude) + 1;
    const marker = L.circleMarker([point.latitude, point.longitude], {
      radius: rank ? 9 : (regional ? 4 : 7),
      color,
      weight: rank ? 2 : 0.6,
      fillColor: color,
      fillOpacity: 0.85,
      bubblingMouseEvents: false,
    }).bindPopup(popup).addTo(overlay);
    if (rank) marker.bindTooltip(String(rank), {permanent: true, direction: 'center', className: 'sample-rank'});
    sampleMarkers.set(`${point.latitude},${point.longitude}`, marker);
  }
}

function changed(recenter = false) {
  if (busy) return;
  clearResult();
  draw(recenter);
  $('status').textContent = $('species').value
    ? 'Ready to explore'
    : 'Train or enable a model to explore this state';
}

function mode(value) {
  if (busy) return;
  regional = value;
  $('point-mode').setAttribute('aria-pressed', String(!value));
  $('area-mode').setAttribute('aria-pressed', String(value));
  $('radius-field').hidden = !value;
  changed(true);
}

function changeRegion(resetLocation = true, preferredSpecies = '') {
  if (!config) return;
  const state = config.regions.find(item => item.code === $('region').value);
  if (!state) return;

  const currentSpecies = preferredSpecies || $('species').value;
  $('species').replaceChildren(...state.species.map(name => new Option(name, name)));
  if (state.species.includes(currentSpecies)) {
    $('species').value = currentSpecies;
  } else if (state.species.includes('Fisher')) {
    $('species').value = 'Fisher';
  }

  if (resetLocation) {
    $('latitude').value = state.center[0];
    $('longitude').value = state.center[1];
  }

  $('species-note').textContent = state.species.length
    ? `${state.species.length} species ready to explore`
    : 'Train or enable a model to explore this state';
  if (state.unavailable_models?.length) {
    $('species-note').textContent += ` · ${state.unavailable_models.join(', ')} need retraining in Saved models to run on this computer`;
  }
  $('analyze').disabled = !state.species.length;
  changed(resetLocation);
}

function metric(value, title) {
  const box = document.createElement('div');
  box.className = 'metric';
  const strong = document.createElement('strong');
  strong.textContent = value;
  const caption = document.createElement('span');
  caption.textContent = title;
  box.append(strong, caption);
  $('metrics').append(box);
}

function showResult(data) {
  result = data;
  const area = data.analysis_type === 'regional';
  $('empty').hidden = true;
  $('result').hidden = false;
  $('result-kind').textContent = area ? 'SURROUNDING AREA' : 'YOUR LOCATION';
  // New assessments always start with the concise result, not expanded details.
  $('assessment-details').open = false;
  $('result-title').textContent = area
    ? `${data.species} · ${data.radius_km} km radius`
    : data.species;
  $('assessment-origin').textContent = data.recorded_at
    ? `Recorded Amherst example · generated ${data.recorded_at.slice(0, 10)} · use Analyze habitat to recompute locally`
    : 'Analyzed on this computer using local habitat data';
  $('metrics').replaceChildren();
  $('metrics').hidden = !area;

  if (area) {
    const scored = (data.points || []).filter(p => p.status === 'ok' && Number.isFinite(p.percentile));
    metric(scored.length ? ordinal(scored.reduce((sum, p) => sum + p.percentile, 0) / scored.length) : '—', 'Mean relative percentile');
    metric(String(data.evaluated_points), 'Points scored');
    if (data.unavailable_points) metric(String(data.unavailable_points), 'Unavailable');
    $('point-percentile').hidden = true;
  } else {
    const percentile = Math.max(0, Math.min(100, Number(data.percentile) || 0));
    $('point-percentile').hidden = false;
    $('percentile-value').textContent = `${ordinal(percentile)} percentile`;
    $('percentile-category').textContent = data.category || '';
    $('percentile-marker').style.left = `${percentile}%`;
    $('percentile-track').setAttribute('aria-valuenow', String(percentile));
    $('percentile-track').setAttribute(
      'aria-valuetext',
      `${Math.round(percentile)}th percentile, ${data.category || 'habitat suitability'}`
    );
    $('percentile-copy').textContent =
      `This location scores higher than about ${Math.round(percentile)}% of the model’s comparison locations for ${data.species}`;
  }

  $('result-note').textContent =
    `${data.model} · ${Number(data.training_observations || 0).toLocaleString()} recorded sightings` +
    (area && !data.evaluated_points
      ? ' · No grid points could be evaluated — try another location or a smaller radius'
      : '');

  $('scores').hidden = !area;
  $('conditions').hidden = area;
  $('score-rows').replaceChildren();
  $('feature-values').replaceChildren();

  for (const point of data.points || []) {
    const tr = document.createElement('tr');
    const ok = point.status === 'ok';
    for (const value of [
      coords(point.latitude, point.longitude),
      ok ? number(point.score) : '—',
      ok ? String(point.percentile) : '—',
      ok ? point.category : 'Unavailable',
    ]) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.append(td);
    }
    $('score-rows').append(tr);
  }

  for (const [name, value] of Object.entries(data.features || {})) {
    const term = document.createElement('dt');
    const detail = document.createElement('dd');
    term.textContent = friendlyFeature(name);
    detail.textContent = featureValue(name, value);
    $('feature-values').append(term, detail);
  }

  $('deep-dive-cta-copy').textContent = area
    ? `Investigate specific buildings, roads, and terrain in this ${data.radius_km} km region.`
    : 'Your score is for one point. Deep Dive investigates buildings, roads, and terrain in the surrounding 10 km radius—not just this point.';
  if (data.recorded_at) $('deep-dive-cta-copy').textContent += ' This runs a fresh analysis with your local data.';
  $('status').textContent = area
    ? 'Assessment complete — select a map point for details'
    : 'Assessment complete';
  renderPointInsights(data);
  renderEvidence(data);
  renderSurvey(data);
  $('legend').hidden = !area;
  $('next-step').textContent = area
    ? 'Before a field visit, check access and habitat on the ground at the shortlisted locations'
    : 'Use these comparisons to guide a closer look at the habitat on the ground';
  draw();
}

function featureValue(name, value) {
  if (!Number.isFinite(value)) return 'Unavailable';
  if (name.includes('fraction')) return `${(value * 100).toFixed(1)}%`;
  if (name.includes('impervious')) return `${value.toFixed(1)}%`;
  if (name.includes('slope')) return `${value.toFixed(1)}°`;
  if (name.includes('distance') || name.includes('elevation') || name.includes('ruggedness')) return `${Math.round(value).toLocaleString()} m`;
  return value.toFixed(3);
}

function renderPointInsights(data) {
  const insights = data.insights;
  $('point-insights').hidden = data.analysis_type === 'regional' || !insights;
  $('influence-rows').replaceChildren();
  if (insights?.error) researchRow($('influence-rows'), 'Interpretation unavailable', insights.error);
  for (const item of (insights?.influences || []).slice(0, 4)) {
    researchRow($('influence-rows'), friendlyFeature(item.feature),
      `Here: ${featureValue(item.feature, item.current)} · typical: ${featureValue(item.feature, item.reference)} · ` +
      `${item.effect >= 0 ? 'Raises' : 'Lowers'} the model score by ${(Math.abs(item.effect) * 100).toFixed(1)} points compared with the typical value`);
  }
  $('scenario-panel').hidden = data.analysis_type === 'regional' || !insights || Boolean(insights.error);
  renderScenarios();
}

function renderScenarios() {
  const parent = $('scenario-cards');
  parent.replaceChildren();
  const fraction = Number($('scenario-level').value || .25);
  const scenarios = (result?.insights?.scenarios || []).filter(s => s.fraction === fraction);
  for (const scenario of scenarios) {
    const card = document.createElement('article');
    card.className = 'scenario-card';
    const title = document.createElement('h4');
    title.textContent = scenario.title.replace('developed cover', 'developed land').replace('impervious surface', 'pavement and rooftops');
    const comparison = document.createElement('div');
    comparison.className = 'scenario-comparison';
    const baseline = document.createElement('span');
    baseline.textContent = `${ordinal(result.percentile)} →`;
    const outcome = document.createElement('strong');
    outcome.textContent = `${ordinal(scenario.percentile)} percentile`;
    comparison.append(baseline, outcome);
    const caption = document.createElement('p');
    caption.className = 'hint';
    const delta = scenario.percentile - result.percentile;
    caption.textContent = `${delta > 0 ? '+' : ''}${delta} percentile points`;
    card.append(title, comparison, caption);
    for (const change of scenario.changes) {
      const p = document.createElement('p');
      p.className = 'scenario-change';
      p.textContent = `${friendlyFeature(change.feature)}: ${featureValue(change.feature, change.before)} → ${featureValue(change.feature, change.after)}`;
      card.append(p);
    }
    if (scenario.outside_training_range?.length) {
      const warning = document.createElement('p');
      warning.className = 'notice';
      warning.textContent = 'This result is less reliable because the change goes beyond the conditions the model learned from';
      card.append(warning);
    }
    parent.append(card);
  }
  if (!scenarios.length) researchRow(parent, 'No changes available here',
    'These comparisons need developed land or paved surfaces that can be changed in the model');
}

function renderEvidence(data) {
  const e = data.evidence;
  $('evidence-panel').hidden = !e;
  $('evidence-stats').replaceChildren();
  if (!e) return;
  const parent = $('evidence-stats');
  parent.append(reviewStat('Recorded sightings', Number(e.presence_count).toLocaleString()),
    reviewStat('Background locations', Number(e.background_count).toLocaleString()), validationHelp(e, data.model));
  $('evidence-copy').textContent = 'The model learns from recorded sightings and nearby environmental conditions, with results checked on locations left out of training.';
  $('evidence-sources').textContent = (e.sources || []).join(' · ');
}

function validationHelp(metrics, model = '') {
  const details = document.createElement('details');
  details.className = 'metric-help';
  const summary = document.createElement('summary');
  summary.textContent = 'How was the model checked?';
  const intro = document.createElement('p');
  intro.textContent = `${model ? `${model} · ` : ''}` +
    (metrics.folds ? `Tested in ${metrics.folds} groups of locations held out of training. ` : '') +
    'These checks compare sightings with background locations without telling us the chance an animal is here.';
  const stats = document.createElement('div');
  stats.className = 'evidence-stats';
  stats.append(reviewStat('ROC-AUC', number(metrics.roc_auc)),
    reviewStat('PR-AUC', number(metrics.pr_auc)), reviewStat('PR reference', number(metrics.pr_reference)));
  details.append(summary, intro, stats);
  for (const [title, copy] of [
    ['What is ROC-AUC?', 'ROC-AUC checks whether recorded sightings rank above background locations, where 1 means perfect separation in the test data and 0.5 is no better than chance. Background locations can still contain the species.'],
    ['What is PR-AUC?', 'PR-AUC measures how well the model separates sightings from background locations as the score cutoff changes, with higher values meaning better performance on this check. Compare it with the PR reference, which depends on the share of sightings in the test data.'],
  ]) {
    const help = document.createElement('details');
    const label = document.createElement('summary');
    label.textContent = title;
    const explanation = document.createElement('p');
    explanation.textContent = copy;
    help.append(label, explanation);
    details.append(help);
  }
  return details;
}

function surveySamples(data) {
  if (data?.analysis_type !== 'regional') return [];
  return (data.points || []).filter(p => p.status === 'ok' && Number.isFinite(p.percentile))
    .sort((a, b) => b.percentile - a.percentile || b.score - a.score).slice(0, 3);
}

function renderSurvey(data) {
  const samples = surveySamples(data);
  $('survey-panel').hidden = !samples.length;
  $('survey-list').replaceChildren();
  $('survey-copy').textContent = `Start with these high-scoring samples for ${data.species}, spaced about ${data.grid_spacing_km} km apart, and check access and habitat before visiting`;
  samples.forEach((point, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'survey-sample';
    const strong = document.createElement('strong');
    strong.textContent = `${index + 1}. ${ordinal(point.percentile)} percentile · ${point.category}`;
    const detail = document.createElement('span');
    detail.textContent = `${coords(point.latitude, point.longitude)} · Locate on map ↗`;
    button.append(strong, detail);
    button.addEventListener('click', () => {
      if (!map) return;
      map.setView([point.latitude, point.longitude], 13);
      sampleMarkers.get(`${point.latitude},${point.longitude}`)?.openPopup();
      $('map').scrollIntoView({behavior: scrollBehavior(), block: 'center'});
    });
    $('survey-list').append(button);
  });
}

$('scenario-level').addEventListener('change', renderScenarios);
$('print-report').addEventListener('click', () => result && printCurrentReport('result', 'print-report'));

async function loadExample() {
  if (busy || deepDiveBusy) return;
  $('try-example').disabled = true;
  error('');
  try {
    const demo = await api('/api/demo');
    const assessment = demo.point;
    $('region').value = 'MA';
    changeRegion(false, assessment.species);
    $('latitude').value = assessment.latitude;
    $('longitude').value = assessment.longitude;
    $('radius').value = '10';
    mode(false);
    $('scenario-level').value = '0.25';
    showResult({...assessment, recorded_at: demo.recorded_at});
    draw(true);
    $('result').scrollIntoView({behavior: scrollBehavior(), block: 'start'});
  } catch (exc) { error(exc.message); }
  finally {
    $('try-example').disabled = false;
  }
}
$('try-example').addEventListener('click', loadExample);

function friendlyFeature(name) {
  return name
    .replace('forest_fraction', 'Forest cover')
    .replace('developed_fraction', 'Developed land')
    .replace('wetland_fraction', 'Wetland cover')
    .replace('open_water_fraction', 'Open water')
    .replace('mean_impervious', 'Pavement and rooftops')
    .replace('distance_to_water_m', 'Distance to water')
    .replace('distance_to_road_m', 'Distance to roads')
    .replace('elevation_m', 'Elevation')
    .replace('mean_slope', 'Average slope')
    .replace('terrain_ruggedness', 'Terrain roughness')
    .replaceAll('_', ' ')
    .replace(/\b250m\b/g, '250 m')
    .replace(/\b1000m\b/g, '1 km')
    .replace(/ (250 m|1 km)$/, ' within $1');
}

function researchMetric(parent, value, title) {
  const box = document.createElement('div');
  box.className = 'research-metric';
  const strong = document.createElement('strong');
  strong.textContent = value;
  const caption = document.createElement('span');
  caption.textContent = title;
  box.append(strong, caption);
  parent.append(box);
}

function researchRow(parent, title, detail) {
  const row = document.createElement('div');
  row.className = 'research-row';
  const strong = document.createElement('strong');
  strong.textContent = title;
  const span = document.createElement('span');
  span.textContent = uiPhrase(detail);
  row.append(strong, span);
  parent.append(row);
}

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

  // Place-focused view: do not hide named features under 81 sample markers.
  drawDeepDivePlaces(report);
  // Leaflet must measure the *visible* element before computing the viewport.
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
  // Display the requested region immediately, before any data download/model work.
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

async function printCurrentReport(section, buttonId) {
  const button = $(buttonId);
  const label = button.textContent;
  const isDeepDive = section === 'deep-dive-content';
  const page = $(isDeepDive ? 'deep-dive-page' : 'explore-page');
  const report = isDeepDive ? deepDiveResult : result;
  const closed = [...document.querySelectorAll(`#${section} details:not([open])`)];
  closed.forEach(detail => { detail.open = true; });
  button.disabled = true;
  button.textContent = 'Preparing print…';
  document.body.classList.toggle('preparing-print', true);
  try {
    preparePrintMaps();
    await waitForMapTiles(isDeepDive ? deepDiveMap : map);
    if (!page.hidden && !$(section).hidden && !$('app-shell').hidden &&
      report === (isDeepDive ? deepDiveResult : result)) window.print();
  } finally {
    document.body.classList.toggle('preparing-print', false);
    restorePrintMaps();
    closed.forEach(detail => { detail.open = false; });
    button.textContent = label;
    button.disabled = false;
  }
}

function waitForMapTiles(view) {
  const pending = [];
  view?.eachLayer?.(layer => {
    if (!layer.isLoading?.()) return;
    pending.push(new Promise(resolve => {
      let timer;
      const done = () => {
        layer.off('load', done);
        clearTimeout(timer);
        resolve();
      };
      layer.once('load', done);
      timer = setTimeout(done, 4000);
    }));
  });
  return Promise.all(pending);
}

function preparePrintMaps() {
  if (printMapViews) return;
  printMapViews = [];
  for (const [view, page, bounds] of [
    [map, 'explore-page', null],
    [deepDiveMap, 'deep-dive-page', deepDiveBounds],
  ]) {
    if (!view || $(page).hidden) continue;
    view.stop?.();
    const center = view.getCenter();
    const zoom = view.getZoom();
    printMapViews.push({view, center, zoom});
    view.invalidateSize({pan: false, animate: false});
    if (bounds) view.fitBounds(bounds, {padding: [18, 18], animate: false});
    else view.setView(center, zoom, {animate: false});
  }
}

function restorePrintMaps() {
  for (const {view, center, zoom} of printMapViews || []) {
    view.stop?.();
    view.invalidateSize({pan: false, animate: false});
    view.setView(center, zoom, {animate: false});
  }
  printMapViews = null;
}

window.addEventListener?.('beforeprint', preparePrintMaps);
window.addEventListener?.('afterprint', () => setTimeout(restorePrintMaps, 0));
$('deep-dive-export').addEventListener('click', () => {
  if (!deepDiveResult) return;
  downloadReport(deepDiveResult, 'deep-dive');
});
async function poll(id, version) {
  if (activeJob !== id || revision !== version) return;
  try {
    const job = await api(`/api/jobs/${id}`);
    if (activeJob !== id || revision !== version) return;
    error('');
    if (job.status === 'running') {
      setTimeout(() => poll(id, version), 600);
      return;
    }
    activeJob = null;
    setBusy(false);
    if (job.status === 'complete') {
      showResult(job.result);
    } else if (job.status === 'cancelled') {
      $('status').textContent = 'Analysis cancelled — ready when you are';
    } else {
      error(job.error || 'Analysis unavailable');
      $('status').textContent = 'Analysis unavailable';
    }
  } catch (exc) {
    if (activeJob !== id || revision !== version) return;
    if (exc.status && exc.status < 500) {
      activeJob = null;
      setBusy(false);
      error(exc.message);
      $('status').textContent = 'Analysis unavailable — reload if the server restarted';
      return;
    }
    error('Retrying after a connection interruption — you can still cancel the analysis');
    setTimeout(() => poll(id, version), 2000);
  }
}

$('analysis-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  let request;
  try {
    request = selection();
  } catch (exc) {
    error(exc.message);
    return;
  }

  clearResult();
  draw();
  const version = revision;
  setBusy(true);
  try {
    const job = await api('/api/jobs', request);
    activeJob = job.id;
    $('cancel').disabled = false;
    poll(job.id, version);
  } catch (exc) {
    setBusy(false);
    error(exc.message);
    $('status').textContent = 'Could not start analysis';
  }
});

$('cancel').addEventListener('click', async () => {
  if (!activeJob) return;
  const id = activeJob;
  const version = ++revision;
  $('cancel').disabled = true;
  try {
    await api(`/api/jobs/${id}/cancel`, {});
    if (activeJob !== id) return;
    revision += 1;
    activeJob = null;
    setBusy(false);
    error('');
    $('status').textContent = 'Analysis cancelled — ready when you are';
  } catch (exc) {
    error(`Could not cancel: ${exc.message}`);
    if (activeJob === id) {
      $('cancel').disabled = false;
      poll(id, version);
    }
  }
});

function downloadReport(report, kind = 'assessment') {
  const payload = {
    ...report,
    note: 'Suitability is relative, not a probability of species presence.',
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'})
  );
  const link = document.createElement('a');
  link.href = url;
  link.download =
    `wild-locate-${report.species.toLowerCase().replaceAll(' ', '-')}-${kind}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$('export').addEventListener('click', () => { if (result) downloadReport(result); });

$('point-mode').addEventListener('click', () => mode(false));
$('area-mode').addEventListener('click', () => mode(true));
$('region').addEventListener('change', () => changeRegion(true));
$('species').addEventListener('change', () => changed());
$('radius').addEventListener('change', () => changed(true));
for (const id of ['latitude', 'longitude']) {
  $(id).addEventListener('input', () => changed());
  $(id).addEventListener('change', () => draw(true));
}
$('reset-map').addEventListener('click', () => {
  if (!config || !map) return;
  const state = config.regions.find(item => item.code === $('region').value);
  if (state) map.setView(state.center, 7);
});

function showSection(section, focus = null) {
  const studio = section === 'species';
  const deepDive = section === 'deep-dive';
  $('explore-page').hidden = studio || deepDive;
  $('species-page').hidden = !studio;
  $('deep-dive-page').hidden = !deepDive;
  $('explore-nav').setAttribute('aria-current', studio ? 'false' : 'page');
  $('train-species-nav').setAttribute('aria-current', studio && focus !== 'models' ? 'page' : 'false');
  $('models-nav').setAttribute('aria-current', studio && focus === 'models' ? 'page' : 'false');
  const heading = $(deepDive ? 'deep-dive-title' : studio ? 'species-heading' : 'explore-heading');
  heading?.focus({preventScroll: true});

  if (deepDive) {
    setTimeout(() => {
      initDeepDiveMap();
      if (deepDiveMap) deepDiveMap.invalidateSize({pan: false});
    }, 0);
    window.scrollTo({top: 0, behavior: scrollBehavior()});
    return;
  }

  if (!studio) {
    setTimeout(() => map && map.invalidateSize({pan: false}), 0);
    window.scrollTo({top: 0, behavior: scrollBehavior()});
    return;
  }

  if (focus === 'models') {
    setTimeout(() => $('model-library').scrollIntoView({behavior: scrollBehavior(), block: 'start'}), 0);
  } else {
    setTimeout(() => $('training-query').focus(), 0);
  }
}

function reviewStat(title, value) {
  const box = document.createElement('div');
  box.className = 'review-stat';
  const caption = document.createElement('span');
  caption.textContent = title;
  const strong = document.createElement('strong');
  strong.textContent = value;
  box.append(caption, strong);
  return box;
}

function renderModelReview() {
  const record = modelRecords.find(item => item.id === selectedModelId);
  $('review-details').replaceChildren();
  $('model-message').hidden = true;

  if (!record) {
    $('review-species').textContent = modelRecords.length ? 'Select a model' : 'No models found';
    const p = document.createElement('p');
    p.className = 'hint';
    p.textContent = modelRecords.length
      ? 'Choose a species to review its model'
      : 'Train a species to save your first model';
    $('review-details').append(p);
    $('enable-model').disabled = true;
    $('retrain-model').disabled = true;
    $('delete-model').disabled = true;
    return;
  }

  $('review-species').textContent = record.species;
  if (record.ready === false) {
    $('model-message').textContent = uiPhrase(record.reason || 'Train this model again to use it on this computer');
    $('model-message').hidden = false;
  }
  $('review-details').append(
    reviewStat('Model', record.custom ? 'Trained by you' : 'Included with Wild-Locate'),
    reviewStat('Status', record.ready === false ? 'Needs retraining' : (record.enabled ? 'In use' : (record.custom ? 'Ready for review' : 'Available')))
  );

  if (!record.details_error) {
    $('review-details').append(
      reviewStat(
        'Recorded sightings',
        Number(record.presence_count || 0).toLocaleString()
      ),
      validationHelp(record, record.model)
    );
    const note = document.createElement('p');
    note.className = 'review-note';
    note.textContent = record.experimental
      ? 'Treat these results cautiously because the model did not consistently beat the simple reference checks'
      : 'Check the results in the field before relying on this model';
    $('review-details').append(note);
  } else {
    const note = document.createElement('p');
    note.className = 'review-note';
    note.textContent = 'Validation details are unavailable — check the saved model files';
    $('review-details').append(note);
  }

  $('enable-model').disabled = record.enabled || record.ready === false;
  $('retrain-model').disabled = false;
  $('delete-model').disabled = !record.custom;
}

function renderModels(selectId = null) {
  if (selectId) selectedModelId = selectId;
  if (!modelRecords.some(item => item.id === selectedModelId)) {
    selectedModelId = modelRecords[0]?.id || null;
  }

  $('model-count').textContent = `${modelRecords.length} model${modelRecords.length === 1 ? '' : 's'}`;
  $('model-list').replaceChildren();
  for (const record of modelRecords) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'model-row';
    row.setAttribute('aria-pressed', String(record.id === selectedModelId));
    const name = document.createElement('strong');
    name.textContent = record.species;
    const meta = document.createElement('span');
    const status = record.ready === false ? 'Unavailable · retrain to restore' : record.enabled
      ? 'In use'
      : (record.custom ? 'Ready for review' : 'Available');
    const created = record.created_at
      ? ` · ${record.created_at.slice(0, 10)}`
      : '';
    meta.textContent = `${record.source} · ${status}${created}`;
    row.append(name, meta);
    row.addEventListener('click', () => {
      selectedModelId = record.id;
      renderModels();
    });
    $('model-list').append(row);
  }

  renderModelReview();
}

async function loadModels(selectId = null) {
  try {
    const data = await api(`/api/models?region=${encodeURIComponent(managerRegion)}`);
    modelRecords = data.models || [];
    renderModels(selectId);
  } catch (exc) {
    modelRecords = [];
    selectedModelId = null;
    renderModels();
    $('model-message').textContent = uiPhrase(exc.message);
    $('model-message').hidden = false;
  }
}

async function openSpeciesStudio(focus = 'train') {
  if (!config?.authenticated) {
    setAuthMode(false);
    showAuth();
    $('auth-copy').textContent = 'Sign in to train a species or manage your saved models';
    return;
  }
  if (busy || deepDiveBusy) return;
  managerRegion = $('region').value || 'MA';

  $('species-region').replaceChildren(
    ...config.regions.map(state => new Option(state.name, state.code))
  );
  $('species-region').value = managerRegion;

  const state = config.regions.find(item => item.code === managerRegion);
  $('manager-subtitle').textContent =
    `${state?.name || managerRegion} species models`;

  if (!trainingJobId || trainingState?.status === 'completed' || trainingState?.status === 'cancelled') {
    resetTrainingUi();
  }
  await loadModels();
  showSection('species', focus);
}

$('explore-nav').addEventListener('click', () => showSection('explore'));
$('train-species-nav').addEventListener('click', () => openSpeciesStudio('train'));
$('models-nav').addEventListener('click', () => openSpeciesStudio('models'));

$('species-region').addEventListener('change', async () => {
  if (trainingState?.status === 'running') {
    const change = window.confirm('Changing region will cancel the current training operation. Continue?');
    if (!change) {
      $('species-region').value = managerRegion;
      return;
    }
    await cancelTraining();
  }
  managerRegion = $('species-region').value;
  const state = config.regions.find(item => item.code === managerRegion);
  $('manager-subtitle').textContent =
    `${state?.name || managerRegion} species models`;
  resetTrainingUi();
  await loadModels();
});

$('enable-model').addEventListener('click', async () => {
  const record = modelRecords.find(item => item.id === selectedModelId);
  if (!record || record.enabled) return;
  try {
    await api('/api/models/enable', {id: record.id});
    await refreshConfig(true);
    await loadModels(record.id);
    $('model-message').textContent =
      `${record.species} is ready to use in Explore`;
    $('model-message').hidden = false;
  } catch (exc) {
    $('model-message').textContent = uiPhrase(exc.message);
    $('model-message').hidden = false;
  }
});

$('delete-model').addEventListener('click', async () => {
  const record = modelRecords.find(item => item.id === selectedModelId);
  if (!record?.custom) return;
  if (!window.confirm(`Delete the saved model for ${record.species}? This cannot be undone.`)) {
    return;
  }
  try {
    await api('/api/models/delete', {id: record.id});
    await refreshConfig(true);
    await loadModels();
  } catch (exc) {
    $('model-message').textContent = uiPhrase(exc.message);
    $('model-message').hidden = false;
  }
});

$('retrain-model').addEventListener('click', () => {
  const record = modelRecords.find(item => item.id === selectedModelId);
  if (!record) return;
  showSection('species', 'train');
  $('training-query').value = record.species;
  $('training-query').focus();
  startTrainingResolve();
});

function resetTrainingUi() {
  trainingJobId = null;
  trainingState = null;
  resolvedTaxon = null;
  handledCompletionId = null;
  trainingRevision += 1;
  $('training-query').disabled = false;
  $('find-species').disabled = false;
  $('training-match').hidden = true;
  $('training-suggestions').hidden = true;
  $('training-suggestions').replaceChildren();
  $('prepare-training').hidden = true;
  $('download-environment').hidden = true;
  $('start-training').hidden = true;
  $('cancel-training').hidden = true;
  $('training-status').textContent = 'Choose a species to begin';
  $('training-summary').textContent =
    'At least 25 usable wildlife sightings are needed';
  $('training-error').hidden = true;
  $('training-log').hidden = true;
  $('training-log-text').textContent = '';
}

function trainingBusy(value) {
  $('training-query').disabled = value;
  $('find-species').disabled = value;
  $('cancel-training').hidden = !value;
  if (value) {
    $('prepare-training').hidden = true;
    $('download-environment').hidden = true;
    $('start-training').hidden = true;
  }
}

async function handleTrainingCompletion(job) {
  const modelId = job.result?.model_id;
  if (!modelId || handledCompletionId === modelId) return;
  handledCompletionId = modelId;
  await refreshConfig(true);
  await loadModels(modelId);
  $('model-message').textContent =
    'Review your new model, then choose whether to use it for habitat analysis';
  $('model-message').hidden = false;
  $('model-library').scrollIntoView({behavior: scrollBehavior(), block: 'start'});
}

function renderTraining(job) {
  trainingState = job;
  $('training-status').textContent = uiPhrase((job.message || 'Working…')
    .replace('Extracting environmental features', 'Reading habitat data')
    .replace(/^Evaluating .+: spatial fold (\d+) of (\d+)/, 'Checking the model · group $1 of $2')
    .replace('Evaluating candidate models using five spatial folds', 'Training and checking the model')
    .replace(/^Fitting the selected .+ model on all training locations/, 'Finishing the model')
    .replace('Downloading research-grade', 'Downloading verified')
    .replace('Checking the completed model and saving it for review', 'Checking and saving your model'));
  $('training-error').hidden = true;

  const logs = job.logs || [];
  $('training-log').hidden = !logs.length;
  $('training-log-text').textContent = logs.join('\n');

  if (job.status === 'running') {
    trainingBusy(true);
    return;
  }

  trainingBusy(false);
  $('prepare-training').hidden = true;
  $('download-environment').hidden = true;
  $('start-training').hidden = true;

  if (job.status === 'resolved') {
    resolvedTaxon = job.result;
    const groupName = job.result.iconic_taxon_name === 'Reptilia' ? 'Reptile' : 'Mammal';
    $('training-match').textContent =
      `${job.result.common_name} (${job.result.scientific_name}) · ${groupName} · ${config.regions.find(item => item.code === managerRegion)?.name || managerRegion}`;
    $('training-match').hidden = false;
    $('prepare-training').hidden = false;
  } else if (job.status === 'prepared') {
    $('training-match').hidden = false;
    $('training-summary').textContent =
      `${Number(job.result.cleaned_count).toLocaleString()} usable sightings from ${Number(job.result.raw_count).toLocaleString()} downloaded records · Ready to train`;
    $('start-training').hidden = false;
  } else if (job.status === 'initialized') {
    $('training-summary').textContent =
      'Habitat data is ready — check the species sightings to continue';
    $('prepare-training').hidden = !resolvedTaxon;
  } else if (job.status === 'completed') {
    $('training-summary').textContent =
      'Your model is saved and ready to review';
    handleTrainingCompletion(job);
  } else if (job.status === 'cancelled') {
    $('training-summary').textContent =
      'Training cancelled';
  } else if (job.status === 'error') {
    $('training-error').textContent = uiPhrase(job.error || 'Training failed');
    $('training-error').hidden = false;
    if (job.code === 'missing_environment') {
      $('download-environment').hidden = false;
    } else if (resolvedTaxon) {
      $('prepare-training').hidden = false;
    }
  }
}

async function pollTraining(id, version) {
  if (trainingJobId !== id || trainingRevision !== version) return;
  try {
    const job = await api(`/api/training/${id}`);
    if (trainingJobId !== id || trainingRevision !== version) return;
    renderTraining(job);
    if (job.status === 'running') {
      setTimeout(() => pollTraining(id, version), 700);
    }
  } catch (exc) {
    if (trainingJobId !== id || trainingRevision !== version) return;
    trainingBusy(false);
    $('training-error').textContent = uiPhrase(exc.message);
    $('training-error').hidden = false;
  }
}

function clearTrainingSuggestions() {
  $('training-suggestions').hidden = true;
  $('training-suggestions').replaceChildren();
}

function renderTrainingSuggestions(data) {
  const suggestions = data.suggestions || [];
  const container = $('training-suggestions');
  container.replaceChildren();

  if (!suggestions.length) {
    clearTrainingSuggestions();
    $('training-error').textContent =
      `No matching mammals or reptiles have verified sightings in ${data.region_name || managerRegion}`;
    $('training-error').hidden = false;
    $('training-status').textContent = 'Try a more specific or different animal name';
    return;
  }

  for (const suggestion of suggestions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'species-suggestion';

    const name = document.createElement('strong');
    name.textContent = suggestion.common_name;
    const meta = document.createElement('span');
    const groupName = suggestion.iconic_taxon_name === 'Reptilia' ? 'Reptile' : 'Mammal';
    meta.textContent =
      `${suggestion.scientific_name} · ${groupName} · ${Number(suggestion.observation_count || 0).toLocaleString()} sightings in ${data.region_name || managerRegion}`;

    button.append(name, meta);
    button.addEventListener('click', async () => {
      $('training-query').value = suggestion.common_name;
      clearTrainingSuggestions();
      await startTrainingResolve(suggestion.common_name);
    });
    container.append(button);
  }

  container.hidden = false;
  $('training-status').textContent =
    `Choose one of the ${suggestions.length} matching species`;
}

async function searchTrainingSpecies() {
  const query = $('training-query').value.trim();
  if (!query) return;

  trainingRevision += 1;
  resolvedTaxon = null;
  handledCompletionId = null;
  $('training-match').hidden = true;
  clearTrainingSuggestions();
  $('training-error').hidden = true;
  $('find-species').disabled = true;
  $('training-status').textContent =
    `Searching ${config.regions.find(item => item.code === managerRegion)?.name || managerRegion} species…`;

  try {
    const data = await api('/api/species/suggestions', {
      region: managerRegion,
      query,
    });
    renderTrainingSuggestions(data);
  } catch (exc) {
    $('training-error').textContent = uiPhrase(exc.message);
    $('training-error').hidden = false;
    $('training-status').textContent = 'Species search failed';
  } finally {
    $('find-species').disabled = false;
  }
}

async function startTrainingResolve(queryOverride = null) {
  const query = queryOverride || $('training-query').value.trim();
  if (!query) return;
  trainingRevision += 1;
  const version = trainingRevision;
  resolvedTaxon = null;
  handledCompletionId = null;
  $('training-match').hidden = true;
  clearTrainingSuggestions();
  $('training-error').hidden = true;
  trainingBusy(true);
  $('training-status').textContent = 'Finding the species on iNaturalist…';
  try {
    const job = await api('/api/training/start', {
      region: managerRegion,
      query,
    });
    trainingJobId = job.id;
    renderTraining(job);
    pollTraining(job.id, version);
  } catch (exc) {
    trainingBusy(false);
    $('training-error').textContent = uiPhrase(exc.message);
    $('training-error').hidden = false;
  }
}

$('training-form').addEventListener('submit', async event => {
  event.preventDefault();
  await searchTrainingSpecies();
});

$('training-query').addEventListener('input', () => {
  if (!trainingState || trainingState.status !== 'running') {
    clearTrainingSuggestions();
  }
});

async function trainingAction(action) {
  if (!trainingJobId) return;
  trainingRevision += 1;
  const version = trainingRevision;
  try {
    const job = await api(
      `/api/training/${trainingJobId}/action`,
      {action}
    );
    renderTraining(job);
    pollTraining(trainingJobId, version);
  } catch (exc) {
    trainingBusy(false);
    $('training-error').textContent = uiPhrase(exc.message);
    $('training-error').hidden = false;
  }
}

$('prepare-training').addEventListener('click', () => trainingAction('prepare'));
$('download-environment').addEventListener('click', () => trainingAction('initialize'));
$('start-training').addEventListener('click', () => trainingAction('train'));

async function cancelTraining() {
  if (!trainingJobId) return;
  trainingRevision += 1;
  try {
    const job = await api(`/api/training/${trainingJobId}/cancel`, {});
    renderTraining(job);
  } catch (exc) {
    $('training-error').textContent = uiPhrase(exc.message);
    $('training-error').hidden = false;
  }
}

$('cancel-training').addEventListener('click', cancelTraining);

async function init() {
  try {
    const data = await api('/api/config');
    config = data;
    token = data.token;
    enterApp(false);
  } catch (_) {
    $('auth-screen').hidden = false;
    authError('Could not connect to Wild-Locate — check that the terminal server is still running, then reload this page');
  }
}

init();
