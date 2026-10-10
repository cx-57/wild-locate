# Specific places in Deep Dive

The user wants actual roads, buildings, and terrain locations investigated by
the existing habitat model, with a clean map and useful explanations. Keep the
approved forest/ivory UI and its natural copy. No paid AI service or new large
dependencies are needed.

## Design

Use the existing sample explanations to select up to six separated 1 km search
neighborhoods, keeping them inside the selected analysis radius. Retrieve real
OpenStreetMap ways and multipolygons through a bounded Overpass request. Use
local MassDOT road geometry if online mapping fails in Massachusetts. Mapping
is partial and its absence must never imply that buildings or pressures do not
exist. Preserve the existing landscape report when retrieval fails.

Evaluate a small, spatially diverse selection of actual feature locations with
the same fitted model and environmental extractor as the landscape samples.
Rank places by the strongest relevant negative single-condition comparison;
effects overlap and must not be summed as causal damage. Terrain sample
locations remain available offline. Never claim a building caused harm, infer
traffic from a road class, or simulate removal of an individual structure using
the coarse habitat predictors.

The report includes coordinates, geometry, source links, footprint/length when
available, surrounding conditions and reference values, local score/percentile,
specific field checks, and honest inspected-area and failure counts. Use a
compact shortlist with expandable evidence and markers linked to cards. Export
and print include the new section. Old reports remain readable.

## Interfaces and file responsibilities

- `core/mapped_places.py`: `discover_places(areas, latitude, longitude,
  radius_km, region)` returns `{status, features, sources, warnings,
  retrieved_at, searched_areas}`. Each feature has `id`, `kind` (`road`,
  `building`, `habitat`), `name`, `latitude`, `longitude`, GeoJSON `geometry`,
  `source`, `source_url`, `tags`, and optional `length_m`/`area_m2`. Clip
  geometry to the study circle, validate it, keep coordinates finite, and
  bound time, response size, feature count, and persisted cache size.
- `core/place_analysis.py`: choose search areas, select diverse candidates,
  run a supplied scoring callback, produce the ranked and explained report.
  Provide explicit unavailable, no-signal, and partial states.
- `core/deep_dive.py`: reuse its model/extractor in the callback and attach a
  `places` report without changing existing outputs.
- `web/index.html`, `app.js`, `style.css`: render the report, source links,
  evidence and markers; safely handle older reports and missing map data.

## Implementation and verification

1. Add offline fixture tests for geometry parsing/clipping, duplicate IDs,
   multipolygon holes, oversized/partial Overpass responses, bounded requests,
   cache freshness, and local road fallback, then implement retrieval.
2. Add tests for relevant model signals, positive/neutral exclusions, candidate
   limits/diversity, nonfinite results, partial evaluation failures, and honest
   ranking, then implement analysis and Deep Dive integration.
3. Add client tests for cards, source links, map focus, partial/missing data,
   and compatibility with old reports, then implement the existing UI extension.
4. Run Python and Node suites, syntax and diff checks, a live real-data report,
   and desktop/mobile browser checks. Review scientific wording and sources.
   Keep the current preview server running and document how to launch it.
