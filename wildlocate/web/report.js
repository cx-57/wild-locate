'use strict';

// Printing, analysis jobs, and navigation

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

