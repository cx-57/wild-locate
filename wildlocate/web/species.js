'use strict';

// Species training and saved models

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
