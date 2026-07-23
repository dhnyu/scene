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
