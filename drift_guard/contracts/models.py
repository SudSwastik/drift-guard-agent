"""Immutable contract identity and canonical content."""

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DIALECT = "https://json-schema.org/draft/2020-12/schema"
Direction = Literal["request", "response"]


class ContractDocument(BaseModel):
    """On-disk envelope; versions are explicitly named, never inferred."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    api: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    operation: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S*$")
    direction: Direction
    schema_document: dict[str, Any] = Field(alias="schema")


@dataclass(frozen=True)
class Contract:
    """Store canonical bytes so callers cannot mutate a loaded contract."""

    api: str
    version: str
    operation: str
    direction: Direction
    content_hash: str
    canonical_document: bytes

    @property
    def contract_id(self) -> str:
        return f"{self.api}:{self.version}:{self.operation}:{self.direction}"

    @property
    def schema(self) -> dict[str, Any]:
        document: dict[str, Any] = json.loads(self.canonical_document)
        schema: dict[str, Any] = document["schema"]
        return schema
