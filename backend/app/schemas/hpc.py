from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class SearchExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["serial", "parallel", "auto"] = "serial"
    workers: Literal[1, 2, 4, 8] | None = None
    allow_fallback: bool = False


class SearchExecutionMetadata(BaseModel):
    schema_version: str = "hpc_execution_v1"
    requested_mode: Literal["serial", "parallel", "auto"]
    actual_mode: Literal["serial", "parallel", "not_run"]
    requested_workers: int | None = None
    worker_count: int = 0
    worker_pids: list[int] = Field(default_factory=list)
    executing_pids: list[int] = Field(default_factory=list)
    catalogue_generation: str | None = None
    feature_count: int = 0
    algorithm_version: str = "hpc_compute_v1"
    timing_scope: str = "lexical_features_and_fusion"
    context_ms: float = 0
    snapshot_bind_ms: float = 0
    global_statistics_ms: float = 0
    query_hash_ms: float = 0
    context_cache_hit: bool = False
    fusion_ms: float = 0
    feature_wall_ms: float = 0
    startup_ms: float = 0
    admission_queue_ms: float = 0
    dispatch_queue_ms: list[float] = Field(default_factory=list)
    worker_execution_ms: list[float] = Field(default_factory=list)
    search_service_ms: float = 0
    fallback_reason: str | None = None
    fallback_overhead_ms: float = 0
