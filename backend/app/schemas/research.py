"""Bounded research v1 contracts. Operator inputs never include credentials."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StageSpan(StrictModel):
    span_id: str
    parent_id: str | None = None
    stage_name: str
    start_offset_ns: int = Field(ge=0)
    duration_ns: int = Field(ge=0)
    process_id: int
    status: str = "ok"
    attributes: dict[str, Any] = Field(default_factory=dict)


class ExecutionTrace(StrictModel):
    schema_version: Literal["hpc_trace_v1"] = "hpc_trace_v1"
    trace_id: str
    run_id: str | None = None
    dataset_id: str
    dataset_hash: str
    query_id: str
    accepted_intent_hash: str = ""
    source_revision: str
    mode_requested: str
    mode_actual: str = "not_run"
    physical_pool_size: int = 0
    concurrency: int = 1
    timestamp_start: str
    timestamp_end: str = ""
    clock: str = "same_host_perf_counter_ns; offsets from coordinator origin; inclusive spans overlap"
    stage_spans: list[StageSpan] = Field(default_factory=list)
    worker_spans: list[StageSpan] = Field(default_factory=list)
    worker_pids: list[int] = Field(default_factory=list)
    candidate_counts: dict[str, int] = Field(default_factory=dict)
    result_hash: str | None = None
    correctness_status: str = "not_compared"
    fallback_reason: str | None = None
    errors: list[str] = Field(default_factory=list)
    environment_metadata: dict[str, Any] = Field(default_factory=dict)
    measurements: dict[str, Any] = Field(default_factory=dict)


class ResearchOptions(StrictModel):
    version: Literal["hpc_research_v1"] = "hpc_research_v1"
    # Validation traces persist; timed requests collect identical stage timings
    # in both modes, without serializing every full trace to experiment files.
    semantic: Literal["disabled", "server_configured"] = "disabled"


class CostModel(StrictModel):
    currency: str = Field(default="USD", min_length=1, max_length=12)
    instance_hourly: float | None = Field(default=None, ge=0)
    core_hourly: float | None = Field(default=None, ge=0)
    coordinator_cores: int = Field(default=1, ge=0, le=64)
    query_volume: int = Field(default=1_000_000, ge=1, le=10**12)
    startup_sessions: int = Field(default=1, ge=0, le=10**6)
    target_p95_ms: float | None = Field(default=None, gt=0)
    required_qps: float | None = Field(default=None, gt=0)
    ram_limit_mb: float | None = Field(default=None, gt=0)
    utilization: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def exclusive_price(self):
        if self.instance_hourly is not None and self.core_hourly is not None:
            raise ValueError("Choose instance_hourly or core_hourly, not both")
        return self


class AnalysisRequest(StrictModel):
    run_ids: list[str] = Field(min_length=1, max_length=12)
    serial_fraction: float = Field(default=.1, ge=0, le=1)
    cost: CostModel = Field(default_factory=CostModel)

    @field_validator("run_ids")
    @classmethod
    def unique_runs(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("run_ids must be unique")
        return values


class AnalysisDocument(StrictModel):
    schema_version: Literal["hpc_research_analysis_v1"] = "hpc_research_analysis_v1"
    id: str
    created_at: str
    run_ids: list[str]
    model: AnalysisRequest
    # Versioned analytical records retain null/unavailable reasons and evidence
    # references. Request payloads remain strict; generated records are extensible.
    scaling: dict[str, Any]
    overhead: list[dict[str, Any]]
    nlp: list[dict[str, Any]]
    cost: dict[str, Any]


class QualityRequest(StrictModel):
    dataset_id: Literal["studio-1000-v1", "studio-10000-v1", "studio-50000-v1"] = "studio-1000-v1"
    workers: Literal[1, 2, 4, 8] = 2
    k: int = Field(default=10, ge=1, le=100)


class QualityJob(StrictModel):
    id: str
    created_at: str
    finished_at: str | None = None
    status: Literal["queued","running","completed","failed","interrupted"]
    request: QualityRequest
    result: dict[str, Any] | None = None
    error: str | None = None
