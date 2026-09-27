# Conservation Screening Implementation Plan

**Goal:** Implement the user's six-step MVP: identify existing strong habitat and modeled restoration potential among the 81 sampled points.

**Architecture:** Share the existing counterfactual search between point and area predictions. Add per-point insights and a deterministic summary, then render it in both existing interfaces and preserve it in JSON exports.

**Tech stack:** Python, pandas, NumPy, PyQt6, JavaScript, Leaflet, unittest and Node's test runner. No new dependencies.

**Spec:** The user's six-step sequence in this conversation is the approved scope and execution order.

## Constraints and decisions

- Preserve point-analysis scenario behavior and thresholds; no UI edits until core work passes tests.
- Reuse each area's extracted frame; no second environmental extraction.
- Best scenario is the largest positive percentile improvement, with score gain breaking ties. Existing point insights retain their score-based ordering.
- Protection candidates: up to five High/Very High points, descending current percentile. Do not present low suitability as high suitability when all points score poorly.
- Restoration candidates: up to five strictly positive percentile gains. A score increase without a percentile increase is retained as a point scenario, but excluded from this ranking.
- Stable ties preserve sampled-point order. Candidate records carry point_index to address the original map marker, including when unavailable points are interspersed.
- Unavailable points do not contribute to distributions or rankings. Insight errors preserve valid suitability results and are displayed distinctly from no positive scenario.
- No weighted scores, patches, corridors, causal claims, or LLM integration.
- Work in the user's existing workspace; preserve pre-existing untracked docs. No commits or external publication needed.

## Tasks

- [ ] 1. Extract restoration_scenarios(model, frame, comparison_scores, score); characterize existing forest/impervious behavior, thresholds, unsupported features, mutation safety and nonfinite outputs in tests/test_conservation.py.
- [ ] 2. Extend predict_area with per-point insights and restoration fields. Test frame/extraction reuse, partial coverage, errors, state-specific extraction, and strict JSON serialization.
- [ ] 3. Add summarize_conservation(points), distribution counts and percentages, protection/restoration rankings and result conservation field. Test ties, empty input, missing scenarios and unavailable points.
- [ ] 4. Add Conservation Screening to browser and desktop, with cards addressing existing markers. Verify click selection, stale-result cleanup, empty states and mapping with unavailable points.
- [ ] 5. Show selected-point current score/category/percentile, local drivers and best scenario with changed variables and percentile transition. Use separate result-point selection so it never changes the analysis center or invalidates results.
- [ ] 6. Preserve screening and per-point insights in exports; include interpretation limitations and document meanings/ranking in README.

## Verification

Run targeted Python tests after each core step; Node UI tests and Qt UI tests after UI changes. Finish with the full Python suite, Node tests, JS syntax check, compileall and git diff --check. Review the complete diff independently after implementation.

## Progress

Initial inspection complete. Existing Python and browser tests started before edits.
