# Spatial Scene Representation Learning

Implementation repository for spatial-scene representation learning.

This repository contains source code, reusable scripts, configuration
templates, and tests. Research documents, reports, data, trained models,
and generated artifacts are maintained locally and are not versioned.

## Spatial Split

The split hierarchy uses Seoul districts for train/validation/test assignment,
2 km blocks for processing and storage, and 500 m windows for model scenes.
This evaluates spatial generalization to districts that were not used for
training. The district assignment itself is fixed in M1 and reused unchanged
for M2 full materialization.

See
[`split_and_scene_contract.md`](docs/contracts/split_and_scene_contract.md) for
the split contract,
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md) for milestone gates,
and [`decision_log.md`](docs/decisions/decision_log.md) for D-005 approval
provenance.

## Project Foundation

M1.1 provides the Python 3.14 package foundation for typed configuration,
read-only input boundaries, structured logging, KST run metadata, reports, and
the CLI. It does not read GIS sources or create scenes.

Run the unit tests:

```bash
python -m pytest
```

Inspect the CLI:

```bash
PYTHONPATH=src python -m scene.cli --help
```

## Canonical Storage

D-011A fixes M1-M2 geometry and spatial review outputs as GeoPackage, tabular
manifests as Zstandard Parquet, resolved configuration as YAML, run summaries
as JSON, and miniature raster fixtures as GeoTIFF. Original rasters remain
read-only references. Training cache and shard formats remain open under
D-011B.

See
[`implementation_contract.md`](docs/contracts/implementation_contract.md) for
the storage boundary and
[`decision_log.md`](docs/decisions/decision_log.md) for approval provenance.

## Source Inventory

M1.2 registers every approved input in `configs/project.yaml`, computes full
SHA-256 hashes, extracts vector or raster metadata without canonical conversion,
and writes Zstandard Parquet, JSON, and Markdown inventory outputs. Later
milestones must use this registry rather than unregistered paths.

```bash
PYTHONPATH=src python -m scene.cli inventory --config configs/project.yaml
```

The command treats all registered sources as read-only and continues scanning
after an individual validation failure.

## Canonical Schema Validation

M1.3 maps every valid M1.2 registry entry to the pre-ID Canonical DataFrame
declared in
[`canonical_schema.yaml`](docs/contracts/canonical_schema.yaml). It validates
required columns, Arrow dtypes, nullability, CRS, and geometry type, then writes
source-specific Zstandard Parquet frames plus a JSON manifest and Markdown
report. Raster entries propagate metadata only; raster pixels are not copied.

```bash
PYTHONPATH=src python -m scene.cli canonical --config configs/project.yaml
```

M1.3 does not create adapters, stable IDs, district splits, scenes, clipping,
relations, tensors, or models. Its detailed boundary and gates are defined in
[`implementation_contract.md`](docs/contracts/implementation_contract.md) and
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md).

## Miniature Dataset

M1.8 creates a deterministic, candidate-only integration fixture from the
immutable M1.7 scene set. It selects three scenes per split by canonical grid
order, resolves intersecting Building, Road Link, Road Node, and POI references
through the M1.5 stable ID registry, and links Landcover and DEM metadata.

```bash
PYTHONPATH=src python -m scene.cli miniature create \
  --config configs/project.yaml
```

The outputs contain tabular IDs and raster source references only. They do not
copy geometry, read raster pixels, clip objects, create observations, relations,
tensors, embeddings, or model inputs. See
[`implementation_contract.md`](docs/contracts/implementation_contract.md) and
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md) for the exact M1.8
boundary.

## Release Validation

M1.9 replays the M1 CLI chain into new run directories and audits content
determinism, geometry, IDs, manifests, provenance, storage, repository health,
and immutable inputs. It does not modify an approved M1 artifact or create
observations, raster crops, relations, tensors, embeddings, or model inputs.

```bash
PYTHONPATH=src python -m scene.cli release validate \
  --config configs/project.yaml
```

The timestamped report records either release decision `A` or `B`; a failed
contract hash or blocking Open decision prevents data-producing M2 work.
Source-free M2.1 contract validation remains possible. Detailed gates are in
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md).

## Scene Observation Contract

M2.1 fixes the logical observation hierarchy, deterministic observation IDs,
closed-set inclusion, clip-derived measures, road part ordering, explicit
missing-value states, and geometry/attribute storage projections. It validates
only a synthetic EPSG:5186 fixture and does not read project GIS sources or
materialize observations.

```bash
PYTHONPATH=src python -m scene.cli observations validate-contract \
  --config configs/project.yaml \
  --schema docs/contracts/scene_observation_schema.yaml \
  --fixture tests/fixtures/observations/m2_1_scene_observation_fixture.yaml
```

The normative details are in
[`scene_observation_contract.md`](docs/contracts/scene_observation_contract.md)
and the machine-readable
[`scene_observation_schema.yaml`](docs/contracts/scene_observation_schema.yaml).
D-004 and D-006 remain blocking gates for actual road observation
materialization.

## Building Adapter

M1.4.1 reads only the validated M1.3 building geometry and attribute frames and
exposes them as an unjoined `BuildingDataset`. It validates the full
MultiPolygon WKB frame, EPSG:5186, bbox, canonical attribute schema, source
metadata, and provenance. Serialization produces an inspection/archive
GeoPackage, a Zstandard attribute Parquet file, and JSON metadata.

```bash
PYTHONPATH=src python -m scene.cli buildings --config configs/project.yaml
```

This step does not join geometry and attributes, create stable IDs, calculate
observed area, or read road, POI, or raster canonical frames.

## Road Adapter

M1.4.2 reads only the validated M1.3 road link and node frames and exposes
unjoined geometry and attribute projections through `RoadLinkDataset` and
`RoadNodeDataset`. It validates all LineString and Point WKB, EPSG:5186, bbox,
canonical attribute schemas, source metadata, and provenance. Serialization
produces two layers in one inspection/archive GeoPackage, separate Zstandard
attribute Parquet files, and JSON metadata.

```bash
PYTHONPATH=src python -m scene.cli roads --config configs/project.yaml
```

The current canonical schema preserves `road_type`, `road_rank`, and
`source_road_name`. It does not declare bridge, tunnel, or direction fields, so
M1.4.2 records those concepts as unavailable instead of inferring values. This
step does not join geometry and attributes, connect links to nodes, create
topology or stable IDs, or read building, POI, or raster canonical frames.

## POI Adapter

M1.4.3 reads only the validated M1.3 POI geometry and attribute frames and
exposes them as an unjoined `POIDataset`. It validates all Point WKB,
EPSG:5186, canonical schemas, source provenance, `NF_ID` join-key compatibility,
and the six-stage category hierarchy. The attribute archive preserves all six
source labels and adds the contract-defined category path without changing
source rows.

```bash
PYTHONPATH=src python -m scene.cli pois --config configs/project.yaml
```

Serialization produces an inspection/archive GeoPackage, a Zstandard attribute
Parquet file, and JSON metadata. Detailed join diagnostics and category path
encoding are defined in
[`implementation_contract.md`](docs/contracts/implementation_contract.md);
acceptance gates are in
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md). This step does not
join rows, deduplicate records, create POI polygons or geometry embeddings,
create stable IDs, or read raster canonical frames.

## Raster Adapter

M1.4.4 registers the configured Landcover and DEM as read-only raster
references. It reads GDAL header metadata only, validates EPSG:5186, dimensions,
resolution, extent, affine alignment, one-band layout, dtype, NoData, source
hash, size, and mtime, then writes JSON and Zstandard Parquet metadata.

```bash
PYTHONPATH=src python -m scene.cli raster build --config configs/project.yaml
```

The Landcover and DEM grids are diagnosed separately; differing resolution,
origin, or extent does not select a resampling policy. D-007, D-008, and D-009
remain Open. This step does not copy GeoTIFFs or pixels and does not create
clips, windows, tensors, normalization, reprojection, object sampling,
encoders, embeddings, model inputs, or training caches. Detailed boundaries
and gates are in
[`implementation_contract.md`](docs/contracts/implementation_contract.md) and
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md).

## Stable IDs

M1.5 reads only the validated M1.3 building, road link, road node, and POI
geometry frames. It preserves every native ID and generates deterministic
source/canonical IDs plus complete source, schema, run, and configuration
provenance.

```bash
PYTHONPATH=src python -m scene.cli ids build --config configs/project.yaml
```

The command writes Zstandard `ids.parquet`, `provenance.parquet`, and
`ids.json` under `outputs/ids/<run_id>/`. Scene, observation, road-part, and
relation ID rules are exposed as pure factories only; no district assignment,
scene, clipping, relation, tensor, raster extraction, model input, or training
cache is materialized. The exact rules and gates are in
[`id_and_provenance_contract.md`](docs/contracts/id_and_provenance_contract.md)
and [`acceptance_tests.md`](docs/contracts/acceptance_tests.md).

## Seoul District Boundaries

M1.5.1 integrates the registered read-only Korean administrative-boundary
GeoPackage into a new EPSG:5186 Seoul district archive. The same command
preserves and extends the M1.2 inventory and adds the actual-field M1.3
district mapping:

```bash
PYTHONPATH=src python -m scene.cli boundary integrate-seoul-districts \
  --config configs/project.yaml
```

It does not assign train/validation/test districts. M1.6 remains separate and
is governed by
[`split_and_scene_contract.md`](docs/contracts/split_and_scene_contract.md).

## District Assignment

M1.6 uses only the frozen M1.5.1 `seoul_sigungu` canonical layer as its
assignment geometry. It computes reportable district statistics from canonical
building, road, and POI adapter artifacts plus read-only Landcover and DEM,
then runs the seed-`20260723` constrained 15/5/5 search:

```bash
PYTHONPATH=src python -m scene.cli split assign --config configs/project.yaml
```

The accepted assignment is protected by a metadata lock and reused by all
later milestones. M1.6 does not materialize scene footprints, clips, raster
crops, tensors, relations, model inputs, or training caches. The exact rules
are in
[`split_and_scene_contract.md`](docs/contracts/split_and_scene_contract.md) and
[`acceptance_tests.md`](docs/contracts/acceptance_tests.md).

## Scene Footprint Generation

M1.7 reads the immutable district assignment lock and generates only the
center-anchored 500 m scene footprints, split allowable regions, and
scene-to-district mappings:

```bash
PYTHONPATH=src python -m scene.cli scenes generate-footprints \
  --config configs/project.yaml
```

The command does not clip objects, assign POIs, crop rasters, build relations,
or create tensors and training caches. Approved D-018 through D-022 behavior is
defined in
[`split_and_scene_contract.md`](docs/contracts/split_and_scene_contract.md),
[`id_and_provenance_contract.md`](docs/contracts/id_and_provenance_contract.md),
and [`acceptance_tests.md`](docs/contracts/acceptance_tests.md).
