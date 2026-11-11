from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.hpc import SearchExecutionRequest
from app.schemas.research import ResearchOptions

DEFAULT_QUERIES = [
    "wireless headphones under 3000", "usb c charger and cable under 2500", "gentle cleanser under 1000",
    "fragrance free moisturiser under 1500", "mechanical keybord under 5000", "programming practice book",
    "bamboo writing desk", "cotton t shirt", "electric kettle under 2000", "steel water bottle",
    "yoga mat under 1500", "LED desk lamp under 2000",
]


class DemoSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=300)
    execution: SearchExecutionRequest = Field(default_factory=SearchExecutionRequest)


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sizes: list[Literal[1000,10000,50000]] = Field(default_factory=lambda: [1000,10000,50000], min_length=1, max_length=3)
    workers: list[Literal[1,2,4,8]] = Field(default_factory=lambda: [1,2,4,8], min_length=1, max_length=4)
    concurrency: list[Literal[1,8,32]] = Field(default_factory=lambda: [1,8,32], min_length=1, max_length=3)
    scopes: list[Literal["scoring","full"]] = Field(default_factory=lambda: ["scoring","full"], min_length=1, max_length=2)
    queries: list[str] = Field(default_factory=lambda: list(DEFAULT_QUERIES), min_length=1, max_length=24)
    trials: int = Field(default=3, ge=1, le=20)
    warmups: int = Field(default=1, ge=1, le=3)
    session_budget_seconds: int = Field(default=300, ge=1, le=3600)
    max_cells_per_session: int = Field(default=1, ge=1, le=72)
    query_timeout_seconds: int = Field(default=300, ge=1, le=1800)
    memory_limit_mb: int = Field(default=4096, ge=256, le=65536)
    research: ResearchOptions | None = None

    @field_validator("sizes", "workers", "concurrency", "scopes")
    @classmethod
    def unique_values(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("matrix dimensions must be unique")
        return value

    @field_validator("queries")
    @classmethod
    def bounded_queries(cls, value):
        if any(not 2 <= len(q.strip()) <= 300 for q in value):
            raise ValueError("queries must contain 2..300 characters")
        return [q.strip() for q in value]
