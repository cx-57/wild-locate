// Shared state, account controls, and the exploration map

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
    // Return to sign-in even if the server already cleared the session
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

