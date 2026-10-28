"""The result contract accepted from the model."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictModel):
    file: str = Field(min_length=1, max_length=200)
    line: int = Field(ge=1)


class TestCase(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    category: Literal[
        "default", "override", "collection", "validation", "security", "output"
    ]
    basis: Literal["implemented_behavior", "proposed_requirement"]
    purpose: str = Field(min_length=1, max_length=600)
    expected: str = Field(min_length=1, max_length=600)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class Finding(StrictModel):
    severity: Literal["info", "warning"]
    description: str = Field(min_length=1, max_length=800)
    evidence: list[Evidence] = Field(min_length=1, max_length=4)


class TestPlan(StrictModel):
    decision: Literal["add_tests", "update_tests", "no_change"]
    summary: str = Field(min_length=1, max_length=1000)
    test_cases: list[TestCase] = Field(max_length=12)
    findings: list[Finding] = Field(max_length=8)
    limitations: list[Annotated[str, Field(min_length=1, max_length=800)]] = Field(
        min_length=1, max_length=6
    )

    @model_validator(mode="after")
    def check_decision(self):
        if (self.decision == "no_change") != (len(self.test_cases) == 0):
            raise ValueError(
                "Only a no_change decision may have no proposed test cases."
            )
        return self
