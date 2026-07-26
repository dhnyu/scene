"""M4 skeleton schema definitions.

These are implementation schemas for M4 stage-runner metadata. They do not
change the research contracts in docs/contracts.
"""

from __future__ import annotations

M4_APPROVED_DECISIONS = ("D-001", "D-002", "D-003", "D-012", "D-013")
M4_STAGE_IDS = ("M4.1", "M4.2", "M4.3", "M4.3A", "M4.4", "M4.5", "M4.6")

MANIFEST_SCHEMA: dict[str, object] = {
    "schema_id": "scene.m4.stage_manifest.v1",
    "required": [
        "schema_id",
        "milestone",
        "stage_id",
        "stage_status",
        "approved_decisions",
        "created_files",
        "forbidden_outputs",
        "next_stage",
    ],
    "properties": {
        "schema_id": "constant scene.m4.stage_manifest.v1",
        "milestone": "M4",
        "stage_id": "explicit M4 stage identifier",
        "stage_status": "PASS or FAIL for the explicit stage",
        "approved_decisions": "D-001, D-002, D-003, D-012, D-013",
        "created_files": "skeleton files created by the stage",
        "forbidden_outputs": "outputs that must not be generated in M4.1",
        "next_stage": "next explicit stage made ready by this stage",
    },
}

REPORT_SCHEMA: dict[str, object] = {
    "schema_id": "scene.m4.stage_report.v1",
    "required_sections": [
        "Created Files",
        "Directory Layout",
        "CLI",
        "Manifest",
        "Stage Runner",
        "Acceptance Placeholder",
        "Audit",
        "Final Status",
    ],
}

CHECKPOINT_SCHEMA: dict[str, object] = {
    "schema_id": "scene.m4.stage_checkpoint.v1",
    "required": [
        "stage_summary",
        "stage_validation",
        "stage_artifact_manifest",
        "stage_hash_manifest",
        "stage_lineage",
        "PASS marker",
    ],
    "m4_1_policy": (
        "M4.1 defines the checkpoint schema only; it does not materialize "
        "feature tensors, model checkpoints, or canonical M4 feature artifacts."
    ),
}
