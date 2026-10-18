"""Envelopes: the typed JSON each agent must end its answer with."""

from __future__ import annotations

import json
import re
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError


class PlanOutput(BaseModel):
    summary: str
    plan: str = Field(description="The full implementation plan in markdown. Code saves it as plan.md.")
    files_to_touch: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    spec_refs: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list,
                                 description="Blocking questions for the human. Non-empty stops the job.")


class DocUpdate(BaseModel):
    path: str = Field(description="Doc file relative to the repo root, e.g. docs/specs/API.md")
    section: str = Field("", description="Heading text or number of the section; empty = the whole file")
    reason: str = Field(description="Why the doc is now wrong or incomplete")
    proposal: str = Field(description="The exact new text for that section, in markdown")

    @property
    def key(self) -> str:
        return f"{self.path.removeprefix('./')}#{self.section.strip().lower()}"


class BuildOutput(BaseModel):
    summary: str
    changed_files: list[str]
    commit_message: str
    spec_conflict: str | None = Field(None, description="Set only if the task cannot be done without breaking a spec.")
    doc_updates: list[DocUpdate] = Field(default_factory=list,
                                         description="Doc sections your change makes wrong or incomplete. "
                                                     "Do not edit them; propose the new text here.")


class Finding(BaseModel):
    severity: Literal["blocking", "minor"]
    file: str = ""
    message: str


class ReviewOutput(BaseModel):
    verdict: Literal["PASS", "CHANGES"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    doc_drift: list[DocUpdate] = Field(default_factory=list,
                                       description="Doc sections in the pack that no longer match the diff. "
                                                   "Not a blocking finding.")
    doc_rejects: list[str] = Field(default_factory=list,
                                   description="`path#section` of builder doc_updates that are wrong and must not be applied.")


class DocsOutput(BaseModel):
    summary: str
    changed_files: list[str]
    commit_message: str


class DocsReviewOutput(ReviewOutput):
    """Review of the docs step (a separate type, so it never mixes with the code review)."""


T = TypeVar("T", bound=BaseModel)

FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def schema_hint(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2)


def extract_json(text: str) -> dict:
    """Last ```json fenced block, else the last balanced {...} object in the text."""
    for block in reversed(FENCE.findall(text)):
        try:
            v = json.loads(block)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            continue
    end = text.rfind("}")
    while end != -1:
        depth = 0
        for i in range(end, -1, -1):
            if text[i] == "}":
                depth += 1
            elif text[i] == "{":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(text[i:end + 1])
                        if isinstance(v, dict):
                            return v
                    except json.JSONDecodeError:
                        pass
                    break
        end = text.rfind("}", 0, end)
    raise ValueError("no JSON object found in the answer")


def parse(text: str, model: type[T]) -> T:
    """Raise ValueError with a message the agent can act on."""
    data = extract_json(text)
    try:
        return model.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ValueError(f"JSON does not match {model.__name__}: {problems}") from e


def instructions(model: type[BaseModel]) -> str:
    return (f"\n\n## Output contract\n\nEnd your answer with ONE fenced ```json block that matches this "
            f"JSON schema ({model.__name__}). No text after it.\n\n```json\n{schema_hint(model)}\n```\n")
