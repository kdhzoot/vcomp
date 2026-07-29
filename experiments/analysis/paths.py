import os
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path(
    os.environ.get("VCOMP_ARTIFACT_ROOT", EXPERIMENT_ROOT / "artifacts")
)
RESULTS_ROOT = Path(
    os.environ.get("VCOMP_RESULTS_ROOT", EXPERIMENT_ROOT / "results")
)

LOG_LOADS = ARTIFACT_ROOT / "log_loads"
LOG_RUNS = ARTIFACT_ROOT / "log_runs"
LOG_BATCH = ARTIFACT_ROOT / "log_batch"
COVERAGE_DUMPS = ARTIFACT_ROOT / "coverage_dumps"
COMPACTION_LOGS = ARTIFACT_ROOT / "compaction_logs"
