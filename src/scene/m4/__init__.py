"""M4 representation-learning stage skeletons.

M4.1 defines only project structure, schemas, CLI wiring, and stage-runner
metadata. Relative, geometry, neural, tensor, training, SSL, and M5+ behavior is
implemented in later explicit stages.
"""

from scene.m4.workflow import M4_STAGE_IDS, run_m4_stage

__all__ = ["M4_STAGE_IDS", "run_m4_stage"]
