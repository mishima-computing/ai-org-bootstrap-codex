"""Research-derived RFC field registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FIELD_OWNERS = ("requester", "grounding", "either")
REQUIRED_AT_VALUES = ("entrance", "rfc_handoff", "optional")
TECH_STACK_BUILD_STRATEGIES = ("", "engine_based", "framework_based", "from_scratch")
TECH_STACK_CONCRETE_BUILD_STRATEGIES = ("engine_based", "framework_based", "from_scratch")
TECH_STACK_PROVENANCE = ("requester_specified", "ai_deliberated", "unspecified")
TECH_STACK_FIELDS = (
    "build_strategy",
    "engine",
    "framework",
    "language",
    "platform",
    "rationale",
    "provenance",
)


@dataclass(frozen=True)
class FieldRegistryEntry:
    name: str
    role: str
    belongs: str
    must_not: str
    owner: str
    required_at: str
    value_type: str = "string"

    @property
    def description(self) -> dict[str, str]:
        return {
            "role": self.role,
            "belongs": self.belongs,
            "must_not": self.must_not,
            "owner": self.owner,
            "required_at": self.required_at,
        }


FIELD_REGISTRY: tuple[FieldRegistryEntry, ...] = (
    FieldRegistryEntry(
        "raw_request",
        "preserve the original ask verbatim",
        "one-line/rough request, attachments",
        "cleaned-up research prose or inferred requirements",
        "requester",
        "entrance",
    ),
    FieldRegistryEntry(
        "working_title",
        "short tracking handle",
        "concise noun/verb phrase",
        "full proposal/rationale/domain essay",
        "either",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "request_type",
        "route workflow",
        "feature|bug|research|game_app|refactor|policy|unknown",
        "requirements details",
        "either",
        "optional",
    ),
    FieldRegistryEntry(
        "problem_or_motivation",
        "why the change is needed",
        "current pain/opportunity/inadequacy",
        "encyclopedia facts that do not explain the problem",
        "grounding",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "intended_users_or_jobs",
        "who benefits and the job/outcome",
        "personas, user/job story, benefit",
        "staff lists, brand lore, implementation tasks",
        "grounding",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "desired_outcomes_success",
        "success signals",
        "acceptance criteria, success metrics, expected result",
        "implementation plan",
        "grounding",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "affected_area_platform",
        "scope/routing boundary",
        "product area, repo/module, version, target surface",
        "the build-platform choice (that is tech_stack), general context, research notes",
        "either",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "tech_stack",
        "what the deliverable is built ON",
        "structured sub-tags {build_strategy, engine, framework, language, platform, rationale, provenance}",
        "from_scratch as a silent default; scope/routing; domain facts",
        "either",
        "rfc_handoff",
        "tech_stack",
    ),
    FieldRegistryEntry(
        "background_facts",
        "bounded objective context",
        "only facts needed to interpret the request (the named thing, its domain, existing constraints)",
        "source lists, research transcript, motivation, proposal, staff/lore/full-history",
        "grounding",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "constraints_assumptions",
        "planning limits and unverified beliefs",
        "legal/compat constraints, budget/time assumptions, user assumptions",
        "proven facts without uncertainty labels",
        "grounding",
        "optional",
        "string_array",
    ),
    FieldRegistryEntry(
        "references",
        "external pointers",
        "URLs, docs, prior issues, source labels",
        "summaries, quotations, conclusions",
        "either",
        "rfc_handoff",
        "string_array",
    ),
    FieldRegistryEntry(
        "grounding_provenance",
        "research audit trail",
        "search notes, source-derived facts, confidence, unresolved verification gaps",
        "content consumed downstream as product requirement nouns",
        "grounding",
        "rfc_handoff",
    ),
    FieldRegistryEntry(
        "open_questions",
        "blockers for RFC drafting",
        "unknowns, choices needing requester/product decision",
        "future nice-to-haves already out of scope",
        "grounding",
        "rfc_handoff",
        "string_array",
    ),
    FieldRegistryEntry(
        "non_goals_out_of_scope",
        "prevent scope creep",
        "things reasonably expected but excluded",
        "rejected implementation alternatives",
        "grounding",
        "optional",
        "string_array",
    ),
    FieldRegistryEntry(
        "proposal_hint",
        "a requested solution captured WITHOUT treating it as decided",
        '"use React", "like X", "add multiplayer"',
        "final design/specification",
        "requester",
        "optional",
    ),
    FieldRegistryEntry(
        "alternatives_considered",
        "early design space when already known",
        'obvious alternatives, prior art, "do nothing"',
        "being a required entrance field",
        "grounding",
        "optional",
        "string_array",
    ),
)

FIELD_REGISTRY_BY_NAME = {entry.name: entry for entry in FIELD_REGISTRY}
RFC_VIEW_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY)
ENTRANCE_REQUIRED_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "entrance")
RFC_HANDOFF_REQUIRED_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "rfc_handoff")
OPTIONAL_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "optional")
STRING_ARRAY_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.value_type == "string_array")
STRING_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.value_type == "string")


def field_schema(entry: FieldRegistryEntry) -> dict[str, Any]:
    if entry.value_type == "string_array":
        return {"type": "array", "items": {"type": "string"}, "description": entry.description}
    if entry.value_type == "tech_stack":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(TECH_STACK_FIELDS),
            "description": entry.description,
            "properties": {
                "build_strategy": {"type": "string", "enum": list(TECH_STACK_BUILD_STRATEGIES)},
                "engine": {"type": "string"},
                "framework": {"type": "string"},
                "language": {"type": "string"},
                "platform": {"type": "string"},
                "rationale": {"type": "string"},
                "provenance": {"type": "string", "enum": list(TECH_STACK_PROVENANCE)},
            },
        }
    return {"type": "string", "description": entry.description}


def rfc_view_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(RFC_VIEW_FIELDS),
        "properties": {entry.name: field_schema(entry) for entry in FIELD_REGISTRY},
    }


def empty_tech_stack() -> dict[str, str]:
    return {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "No stack was specified at entrance; approach formation must evaluate engine/framework/platform candidates before promotion.",
        "provenance": "unspecified",
    }


def empty_value(entry: FieldRegistryEntry) -> Any:
    if entry.value_type == "string_array":
        return []
    if entry.value_type == "tech_stack":
        return empty_tech_stack()
    return ""


def request_to_raw_request(data: Mapping[str, Any]) -> str:
    value = data.get("raw_request")
    if isinstance(value, str) and value.strip():
        return value
    for key in ("title", "problem", "proposal"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def entrance_defaults(data: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {entry.name: empty_value(entry) for entry in FIELD_REGISTRY}
    normalized.update({key: value for key, value in data.items() if key in FIELD_REGISTRY_BY_NAME})
    normalized["raw_request"] = request_to_raw_request(data)
    return normalized


def validate_tech_stack(value: object, *, require_choice: bool = True) -> bool:
    if not isinstance(value, dict) or set(value) != set(TECH_STACK_FIELDS):
        return False
    if not all(isinstance(value[field], str) for field in TECH_STACK_FIELDS):
        return False
    if value["build_strategy"] not in TECH_STACK_BUILD_STRATEGIES:
        return False
    if value["provenance"] not in TECH_STACK_PROVENANCE:
        return False
    if value["provenance"] == "unspecified":
        unspecified_choice_fields = ("build_strategy", "engine", "framework", "language", "platform")
        return all(not value[field].strip() for field in unspecified_choice_fields)
    if value["build_strategy"] not in TECH_STACK_CONCRETE_BUILD_STRATEGIES:
        return False
    if require_choice and value["provenance"] != "unspecified" and not value["rationale"].strip():
        return False
    if value["build_strategy"] == "from_scratch" and not value["rationale"].strip():
        return False
    return True
