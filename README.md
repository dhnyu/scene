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
