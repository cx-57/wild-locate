'use strict';

// Habitat results and human-readable model explanations

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

