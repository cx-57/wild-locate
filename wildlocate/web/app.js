'use strict';

const $ = id => document.getElementById(id);

let config = null;
let token = null;
let regional = true;
let busy = false;
let activeJob = null;
let result = null;
let revision = 0;
let started = 0;
let elapsedTimer = null;
let map = null;
let overlay = null;
let authCreate = false;

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
const coords = (lat, lon) =>
  `${Math.abs(lat).toFixed(4)}° ${lat >= 0 ? 'N' : 'S'} / ${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? 'E' : 'W'}`;

function error(message) {
  $('error').textContent = message || '';
  $('error').hidden = !message;
}

function authError(message) {
  $('auth-error').textContent = message || '';
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
    const failure = Error(data.error || 'Could not contact the local server.');
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
    ? 'Create a local account for your trained species models and habitat work.'
    : 'Sign in to explore habitat and access the species models saved to your account.';
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

$('auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const username = $('auth-username').value.trim();
  const password = $('auth-password').value;
  if (authCreate && password !== $('auth-confirm').value) {
    authError('Your passwords don’t match.');
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
  try {
    const data = await api('/api/auth/logout', {});
    config = data;
    token = data.token;
  } catch (_) {
    // The server may already have reset. Either way return to the sign-in screen.
  }
  activeJob = null;
  result = null;
  $('species-page').hidden = true;
  $('explore-page').hidden = false;
  setAuthMode(false);
  showAuth();
});

function initMap() {
  if (map || !window.L) {
    if (!window.L) {
      $('map-status').hidden = false;
      $('map-status').textContent = 'Map unavailable. Enter coordinates manually.';
      document.querySelector('.manual').open = true;
    }
    return;
  }

  map = L.map('map', {scrollWheelZoom: false, minZoom: 3, maxZoom: 18})
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
    $('map-status').textContent = 'Map tiles are temporarily unavailable. Enter coordinates manually.';
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
  if (!config || !config.authenticated) {
    showAuth();
    return;
  }
  $('auth-screen').hidden = true;
  $('app-shell').hidden = false;
  $('account-name').textContent = config.username;
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
  $('account-name').textContent = data.username || '';

  $('region').replaceChildren(
    ...data.regions.map(state => new Option(state.name, state.code))
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
  if (!data.authenticated) {
    config = data;
    token = data.token;
    showAuth();
    return;
  }
  applyConfig(data, preserveSelection);
}

function selection() {
  if (!$('latitude').value.trim() || !$('longitude').value.trim()) {
    throw Error('Enter both latitude and longitude.');
  }
  const latitude = Number($('latitude').value);
  const longitude = Number($('longitude').value);
  if (
    !Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
    !Number.isFinite(longitude) || Math.abs(longitude) > 180
  ) {
    throw Error('Latitude must be −90 to 90; longitude must be −180 to 180.');
  }
  if (!$('species').value) {
    throw Error('No enabled model is available for this state. Train or enable a model.');
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
    L.circleMarker([point.latitude, point.longitude], {
      radius: regional ? 4 : 7,
      color,
      weight: 0.6,
      fillColor: color,
      fillOpacity: 0.85,
      bubblingMouseEvents: false,
    }).bindPopup(popup).addTo(overlay);
  }
}

function changed(recenter = false) {
  if (busy) return;
  clearResult();
  draw(recenter);
  $('status').textContent = $('species').value
    ? 'Ready to explore.'
    : 'No enabled model for this state. Train or enable a model.';
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
  } else if (state.species.includes('Bobcat')) {
    $('species').value = 'Bobcat';
  }

  if (resetLocation) {
    $('latitude').value = state.center[0];
    $('longitude').value = state.center[1];
  }

  $('species-note').textContent = state.species.length
    ? `${state.species.length} enabled species model${state.species.length === 1 ? '' : 's'} available.`
    : 'No enabled model for this state. Train or enable a model.';
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
  $('result-kind').textContent = area ? 'REGIONAL ASSESSMENT' : 'POINT ASSESSMENT';
  $('result-title').textContent = area
    ? `${data.species} · ${data.radius_km} km radius`
    : data.species;
  $('metrics').replaceChildren();

  if (area) {
    metric(number(data.mean_score), 'Mean suitability score');
    metric(String(data.evaluated_points), 'Points scored');
    metric(`${data.grid_spacing_km} km`, 'Grid spacing');
    if (data.unavailable_points) metric(String(data.unavailable_points), 'Unavailable');
    $('point-percentile').hidden = true;
  } else {
    metric(number(data.score), 'Suitability score');
    metric(data.category, 'Habitat suitability');

    const percentile = Math.max(0, Math.min(100, Number(data.percentile) || 0));
    $('point-percentile').hidden = false;
    $('percentile-value').textContent = `${Math.round(percentile)}th percentile`;
    $('percentile-category').textContent = data.category || '';
    $('percentile-marker').style.left = `${percentile}%`;
    $('percentile-track').setAttribute('aria-valuenow', String(percentile));
    $('percentile-track').setAttribute(
      'aria-valuetext',
      `${Math.round(percentile)}th percentile, ${data.category || 'habitat suitability'}`
    );
    $('percentile-copy').textContent =
      `This location received a higher habitat-suitability score than approximately ${Math.round(percentile)}% of comparison locations for this species.`;
  }

  $('result-note').textContent =
    `${data.model} · ${Number(data.training_observations || 0).toLocaleString()} training observations` +
    (area && !data.evaluated_points
      ? ' · No grid points could be evaluated. Try another location or a smaller radius.'
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
    term.textContent = name.replaceAll('_', ' ');
    detail.textContent = number(value);
    $('feature-values').append(term, detail);
  }

  $('status').textContent = area
    ? 'Assessment complete. Select a map point for details.'
    : 'Assessment complete.';
  draw();
}

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
      $('status').textContent = 'Analysis cancelled. Ready when you are.';
    } else {
      error(job.error || 'Analysis unavailable.');
      $('status').textContent = 'Analysis unavailable.';
    }
  } catch (exc) {
    if (activeJob !== id || revision !== version) return;
    if (exc.status && exc.status < 500) {
      activeJob = null;
      setBusy(false);
      error(exc.message);
      $('status').textContent = 'Analysis unavailable. Reload if the server restarted.';
      return;
    }
    error('Connection interrupted. Retrying; you can still cancel the analysis.');
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
    $('status').textContent = 'Could not start analysis.';
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
    $('status').textContent = 'Analysis cancelled. Ready when you are.';
  } catch (exc) {
    error(`Could not cancel: ${exc.message}`);
    if (activeJob === id) {
      $('cancel').disabled = false;
      poll(id, version);
    }
  }
});

$('export').addEventListener('click', () => {
  if (!result) return;
  const payload = {
    ...result,
    note: 'Suitability is relative, not a probability of species presence.',
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'})
  );
  const link = document.createElement('a');
  link.href = url;
  link.download =
    `wild-locate-${result.species.toLowerCase().replaceAll(' ', '-')}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

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
  $('explore-page').hidden = studio;
  $('species-page').hidden = !studio;
  $('explore-nav').setAttribute('aria-current', studio ? 'false' : 'page');
  $('train-species-nav').setAttribute('aria-current', studio && focus !== 'models' ? 'page' : 'false');
  $('models-nav').setAttribute('aria-current', studio && focus === 'models' ? 'page' : 'false');

  if (!studio) {
    setTimeout(() => map && map.invalidateSize({pan: false}), 0);
    window.scrollTo({top: 0, behavior: 'smooth'});
    return;
  }

  if (focus === 'models') {
    setTimeout(() => $('model-library').scrollIntoView({behavior: 'smooth', block: 'start'}), 0);
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
      ? 'Choose a model to inspect its validation summary.'
      : 'Train a species to create your first custom model.';
    $('review-details').append(p);
    $('enable-model').disabled = true;
    $('retrain-model').disabled = true;
    $('delete-model').disabled = true;
    return;
  }

  $('review-species').textContent = record.species;
  $('review-details').append(
    reviewStat('Source', record.source),
    reviewStat('Status', record.enabled ? 'Enabled' : (record.custom ? 'Ready for review' : 'Available'))
  );

  if (!record.details_error) {
    $('review-details').append(
      reviewStat('Selected model', record.model || '—'),
      reviewStat(
        'Training locations',
        `${Number(record.presence_count || 0).toLocaleString()} observations + ${Number(record.background_count || 0).toLocaleString()} background`
      ),
      reviewStat('Spatial validation', record.folds ? `${record.folds} folds` : '—'),
      reviewStat('Mean ROC-AUC', Number.isFinite(record.roc_auc) ? record.roc_auc.toFixed(3) : '—'),
      reviewStat('Mean PR-AUC', Number.isFinite(record.pr_auc) ? record.pr_auc.toFixed(3) : '—'),
      reviewStat('PR reference', Number.isFinite(record.pr_reference) ? record.pr_reference.toFixed(3) : '—')
    );
    const note = document.createElement('p');
    note.className = 'review-note';
    note.textContent = record.experimental
      ? 'Validation did not consistently outperform the simple references. Treat this model as experimental.'
      : 'ROC-AUC measures separation from background. PR-AUC summarizes precision and recall and depends on sampling balance; neither metric establishes presence probability.';
    $('review-details').append(note);
  } else {
    const note = document.createElement('p');
    note.className = 'review-note';
    note.textContent = 'Validation details are unavailable. Check the saved model files.';
    $('review-details').append(note);
  }

  $('enable-model').disabled = record.enabled;
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
    row.setAttribute('aria-selected', String(record.id === selectedModelId));
    const name = document.createElement('strong');
    name.textContent = record.species;
    const meta = document.createElement('span');
    const status = record.enabled
      ? 'Enabled'
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
    $('model-message').textContent = exc.message;
    $('model-message').hidden = false;
  }
}

async function openSpeciesStudio(focus = 'train') {
  if (busy) return;
  managerRegion = $('region').value || 'MA';

  $('species-region').replaceChildren(
    ...config.regions.map(state => new Option(state.name, state.code))
  );
  $('species-region').value = managerRegion;

  const state = config.regions.find(item => item.code === managerRegion);
  $('manager-subtitle').textContent =
    `${state?.name || managerRegion} · models and training use the selected region's environmental data.`;

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
    `${state?.name || managerRegion} · models and training use the selected region's environmental data.`;
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
      `${record.species} is now available in the analysis dropdown.`;
    $('model-message').hidden = false;
  } catch (exc) {
    $('model-message').textContent = exc.message;
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
    $('model-message').textContent = exc.message;
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
  $('training-status').textContent = 'Choose a species to begin.';
  $('training-summary').textContent =
    'Training needs at least 25 cleaned observations and uses spatial cross-validation.';
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
    'Training complete. Review the validation results, then enable the model when ready.';
  $('model-message').hidden = false;
  $('model-library').scrollIntoView({behavior: 'smooth', block: 'start'});
}

function renderTraining(job) {
  trainingState = job;
  $('training-status').textContent = job.message || 'Working…';
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
      `${Number(job.result.cleaned_count).toLocaleString()} usable observations from ${Number(job.result.raw_count).toLocaleString()} downloaded records. Environmental data is available; spatial coverage will be checked during training.`;
    $('start-training').hidden = false;
  } else if (job.status === 'initialized') {
    $('training-summary').textContent =
      'Environmental datasets are ready. Confirm the species and check its observations again.';
    $('prepare-training').hidden = !resolvedTaxon;
  } else if (job.status === 'completed') {
    $('training-summary').textContent =
      'Training complete. The model has been saved to your account for review.';
    handleTrainingCompletion(job);
  } else if (job.status === 'cancelled') {
    $('training-summary').textContent =
      'Training cancelled. Your enabled models were not changed.';
  } else if (job.status === 'error') {
    $('training-error').textContent = job.error || 'Training failed.';
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
    $('training-error').textContent = exc.message;
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
      `No mammal or reptile species with research-grade observations in ${data.region_name || managerRegion} matched that search.`;
    $('training-error').hidden = false;
    $('training-status').textContent = 'Try a more specific or different animal name.';
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
      `${suggestion.scientific_name} · ${groupName} · ${Number(suggestion.observation_count || 0).toLocaleString()} ${data.region_name || managerRegion} observations`;

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
    `Choose one of the ${suggestions.length} matching species.`;
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
    $('training-error').textContent = exc.message;
    $('training-error').hidden = false;
    $('training-status').textContent = 'Species search failed.';
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
    $('training-error').textContent = exc.message;
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
    $('training-error').textContent = exc.message;
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
    $('training-error').textContent = exc.message;
    $('training-error').hidden = false;
  }
}

$('cancel-training').addEventListener('click', cancelTraining);

async function init() {
  try {
    const data = await api('/api/config');
    config = data;
    token = data.token;
    if (data.authenticated) {
      enterApp(false);
    } else {
      setAuthMode(false);
      showAuth();
    }
  } catch (_) {
    $('auth-screen').hidden = false;
    authError('Could not connect to Wild-Locate. Check that the terminal server is still running, then reload this page.');
  }
}

init();
