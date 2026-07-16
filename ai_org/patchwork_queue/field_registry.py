"""Research-derived patch series field registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FIELD_OWNERS = ("requester", "grounding", "either")
REQUIRED_AT_VALUES = ("entrance", "patch_series_handoff", "optional")
LINT_SCOPES = ("target", "context")
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
USER_EXPERIENCE_APPLICABILITY = ("user_facing", "not_user_facing")
UX_APPLICABILITY_FIELDS = ("applicability", "not_user_facing_reason")
UX_EXPERIENCE_IDENTITY_FIELDS = (
    "named_reference",
    "genre_conventions",
    "must_resemble",
    "must_not_resemble",
)
UX_PRESENTATION_MODEL_FIELDS = ("camera_and_view", "world_readability", "ui_taxonomy_notes")
UX_CORE_STATUS_SURFACES_FIELDS = (
    "player_status",
    "opposition_status",
    "inventory_resources",
    "objective_progress",
    "location_identity",
)
UX_ENTITY_AFFORDANCES_FIELDS = (
    "interactive_entities",
    "exits_and_transitions",
    "gates_and_locks",
    "hazards_and_bosses",
    "collectibles",
    "decorative_elements",
)
UX_ACTION_FEEDBACK_FIELDS = ("action_verb", "feedback_requirement")
UX_PROGRESSION_LEGIBILITY_FIELDS = (
    "current_goal_visibility",
    "locked_state_feedback",
    "unlocked_state_feedback",
    "flag_observability",
    "ending_state_consistency",
)
UX_HUD_AND_UI_FLOW_FIELDS = (
    "primary_hud",
    "secondary_screens",
    "menu_flow",
    "dialog_flow",
    "failure_and_recovery",
)
UX_VISUAL_LANGUAGE_CONSTRAINTS_FIELDS = (
    "contrast",
    "palette_role",
    "silhouette_readability",
    "labels_and_markers",
    "animation_minimums",
)
UX_ACCESSIBILITY_BASELINE_FIELDS = (
    "controls",
    "text_readability",
    "color_independence",
    "audio_independence",
    "pacing",
)
UX_ACCEPTANCE_TESTS_FIELDS = ("screenshot_checks", "interaction_checks", "playtest_checks")
USER_EXPERIENCE_REQUIREMENTS_FIELDS = (
    "applicability",
    "experience_identity",
    "presentation_model",
    "core_status_surfaces",
    "entity_affordances",
    "action_feedback_matrix",
    "progression_legibility",
    "hud_and_ui_flow",
    "visual_language_constraints",
    "accessibility_baseline",
    "acceptance_tests",
)


@dataclass(frozen=True)
class FieldRegistryEntry:
    name: str
    role: str
    belongs: str
    must_not: str
    owner: str
    required_at: str
    lint_scope: str
    value_type: str = "string"

    @property
    def description(self) -> dict[str, str]:
        # Structured registry semantics for prompt/documentation use (REQUEST_SCHEMA,
        # _field_registry_prompt). This is NOT a JSON-Schema description: codex --output-schema
        # (OpenAI Structured Outputs) rejects a non-string `description` with HTTP 400
        # "... is not of type 'string'". Schema builders must use schema_description() instead.
        return {
            "role": self.role,
            "belongs": self.belongs,
            "must_not": self.must_not,
            "owner": self.owner,
            "required_at": self.required_at,
            "lint_scope": self.lint_scope,
        }

    def schema_description(self) -> str:
        # Flattened, string-valued form of the registry semantics, safe to embed as a
        # JSON-Schema `description` in a codex --output-schema.
        return (
            f"role={self.role}; belongs={self.belongs}; must_not={self.must_not}; "
            f"owner={self.owner}; required_at={self.required_at}; lint_scope={self.lint_scope}"
        )


FIELD_REGISTRY: tuple[FieldRegistryEntry, ...] = (
    FieldRegistryEntry(
        "raw_request",
        "preserve the original ask verbatim",
        "one-line/rough request, attachments",
        "cleaned-up research prose or inferred requirements",
        "requester",
        "entrance",
        "context",
    ),
    FieldRegistryEntry(
        "working_title",
        "short tracking handle",
        "concise noun/verb phrase",
        "full proposal/rationale/domain essay",
        "either",
        "patch_series_handoff",
        "target",
    ),
    FieldRegistryEntry(
        "request_type",
        "route workflow",
        "feature|bug|research|game_app|refactor|policy|unknown",
        "requirements details",
        "either",
        "optional",
        "context",
    ),
    FieldRegistryEntry(
        "problem_or_motivation",
        "why the change is needed",
        "current pain/opportunity/inadequacy",
        "encyclopedia facts that do not explain the problem",
        "grounding",
        "patch_series_handoff",
        "target",
    ),
    FieldRegistryEntry(
        "intended_users_or_jobs",
        "who benefits and the job/outcome",
        "personas, user/job story, benefit",
        "staff lists, brand lore, implementation tasks",
        "grounding",
        "patch_series_handoff",
        "target",
    ),
    FieldRegistryEntry(
        "desired_outcomes_success",
        "success signals",
        "acceptance criteria, success metrics, expected result",
        "implementation plan",
        "grounding",
        "patch_series_handoff",
        "target",
    ),
    FieldRegistryEntry(
        "affected_area_platform",
        "scope/routing boundary",
        "product area, repo/module, version, target surface",
        "the build-platform choice (that is tech_stack), general context, research notes",
        "either",
        "patch_series_handoff",
        "target",
    ),
    FieldRegistryEntry(
        "tech_stack",
        "what the deliverable is built ON",
        "structured sub-tags {build_strategy, engine, framework, language, platform, rationale, provenance}; platform is the user-facing runtime target such as browser or lightweight desktop, or for a deliverable that is not a user-facing product, the deliverable's own target surface such as git-committed specification documents",
        "from_scratch as a silent default; scope/routing; domain facts; org verifier mechanisms such as functional_check or codex in platform (worktree too, when the deliverable is a user-facing product)",
        "either",
        "patch_series_handoff",
        "target",
        "tech_stack",
    ),
    FieldRegistryEntry(
        "user_experience_requirements",
        "patch-series-altitude observable graphics/UI/UX contract derived by grounding",
        "observable behavior, states, feedback channels, named-reference and genre conventions, accessibility baselines, and screenshot/interaction/playtest checks that prove perceivability",
        "exact palettes, sprite dimensions, typography, component names, file names, implementation details, decorative claims with no observable effect, or research prose",
        "grounding",
        "patch_series_handoff",
        "target",
        "user_experience_requirements",
    ),
    FieldRegistryEntry(
        "background_facts",
        "bounded objective context",
        "only facts needed to interpret the request (the named thing, its domain, existing constraints)",
        "source lists, research transcript, motivation, proposal, staff/lore/full-history",
        "grounding",
        "patch_series_handoff",
        "context",
    ),
    FieldRegistryEntry(
        "constraints_assumptions",
        "planning limits and unverified beliefs",
        "compatibility constraints, budget/time assumptions, user assumptions, recorded requester-decision assumptions",
        "proven facts without uncertainty labels",
        "grounding",
        "optional",
        "context",
        "string_array",
    ),
    FieldRegistryEntry(
        "references",
        "external pointers",
        "URLs, docs, prior issues, source labels",
        "summaries, quotations, conclusions",
        "either",
        "patch_series_handoff",
        "context",
        "string_array",
    ),
    FieldRegistryEntry(
        "grounding_provenance",
        "research audit trail",
        "search notes, source-derived facts, confidence, unresolved verification gaps",
        "content consumed downstream as product requirement nouns",
        "grounding",
        "patch_series_handoff",
        "context",
    ),
    FieldRegistryEntry(
        "open_questions",
        "blockers for patch series drafting",
        "unknowns, choices needing requester/product decision",
        "future nice-to-haves already out of scope",
        "grounding",
        "patch_series_handoff",
        "context",
        "string_array",
    ),
    FieldRegistryEntry(
        "non_goals_out_of_scope",
        "prevent scope creep",
        "things reasonably expected but excluded",
        "rejected implementation alternatives",
        "grounding",
        "optional",
        "context",
        "string_array",
    ),
    FieldRegistryEntry(
        "proposal_hint",
        "a requested solution captured WITHOUT treating it as decided",
        '"use React", "like X", "add multiplayer"',
        "final design/specification",
        "requester",
        "optional",
        "context",
    ),
    FieldRegistryEntry(
        "alternatives_considered",
        "early design space when already known",
        'obvious alternatives, prior art, "do nothing"',
        "being a required entrance field",
        "grounding",
        "optional",
        "context",
        "string_array",
    ),
)

FIELD_REGISTRY_BY_NAME = {entry.name: entry for entry in FIELD_REGISTRY}
WORK_ORDER_VIEW_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY)
LINT_TARGET_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.lint_scope == "target")
LINT_CONTEXT_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.lint_scope == "context")
ENTRANCE_REQUIRED_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "entrance")
WORK_ORDER_HANDOFF_REQUIRED_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "patch_series_handoff")
OPTIONAL_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.required_at == "optional")
STRING_ARRAY_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.value_type == "string_array")
STRING_FIELDS = tuple(entry.name for entry in FIELD_REGISTRY if entry.value_type == "string")


def _string_object_schema(fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": {field: {"type": "string"} for field in fields},
    }


def field_schema(entry: FieldRegistryEntry) -> dict[str, Any]:
    if entry.value_type == "string_array":
        return {"type": "array", "items": {"type": "string"}, "description": entry.schema_description()}
    if entry.value_type == "tech_stack":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(TECH_STACK_FIELDS),
            "description": entry.schema_description(),
            "properties": {
                "build_strategy": {"type": "string", "enum": list(TECH_STACK_BUILD_STRATEGIES)},
                "engine": {"type": "string"},
                "framework": {"type": "string"},
                "language": {"type": "string"},
                "platform": {
                    "type": "string",
                    "description": "User-facing runtime target, for example browser or lightweight desktop. Org verification constraints belong in constraints, not here.",
                },
                "rationale": {"type": "string"},
                "provenance": {"type": "string", "enum": list(TECH_STACK_PROVENANCE)},
            },
        }
    if entry.value_type == "user_experience_requirements":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": list(USER_EXPERIENCE_REQUIREMENTS_FIELDS),
            "description": entry.schema_description(),
            "properties": {
                "applicability": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(UX_APPLICABILITY_FIELDS),
                    "properties": {
                        "applicability": {"type": "string", "enum": list(USER_EXPERIENCE_APPLICABILITY)},
                        "not_user_facing_reason": {"type": "string"},
                    },
                },
                "experience_identity": _string_object_schema(UX_EXPERIENCE_IDENTITY_FIELDS),
                "presentation_model": _string_object_schema(UX_PRESENTATION_MODEL_FIELDS),
                "core_status_surfaces": _string_object_schema(UX_CORE_STATUS_SURFACES_FIELDS),
                "entity_affordances": _string_object_schema(UX_ENTITY_AFFORDANCES_FIELDS),
                "action_feedback_matrix": {
                    "type": "array",
                    "items": _string_object_schema(UX_ACTION_FEEDBACK_FIELDS),
                },
                "progression_legibility": _string_object_schema(UX_PROGRESSION_LEGIBILITY_FIELDS),
                "hud_and_ui_flow": _string_object_schema(UX_HUD_AND_UI_FLOW_FIELDS),
                "visual_language_constraints": _string_object_schema(UX_VISUAL_LANGUAGE_CONSTRAINTS_FIELDS),
                "accessibility_baseline": _string_object_schema(UX_ACCESSIBILITY_BASELINE_FIELDS),
                "acceptance_tests": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(UX_ACCEPTANCE_TESTS_FIELDS),
                    "properties": {
                        field: {"type": "array", "items": {"type": "string"}}
                        for field in UX_ACCEPTANCE_TESTS_FIELDS
                    },
                },
            },
        }
    return {"type": "string", "description": entry.schema_description()}


def patch_series_view_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(WORK_ORDER_VIEW_FIELDS),
        "properties": {entry.name: field_schema(entry) for entry in FIELD_REGISTRY},
    }


def empty_tech_stack() -> dict[str, str]:
    return {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "",
        "provenance": "unspecified",
    }


def empty_user_experience_requirements() -> dict[str, Any]:
    return {
        "applicability": {
            "applicability": "not_user_facing",
            "not_user_facing_reason": "Grounding has not derived a user-facing surface yet.",
        },
        "experience_identity": {field: "" for field in UX_EXPERIENCE_IDENTITY_FIELDS},
        "presentation_model": {field: "" for field in UX_PRESENTATION_MODEL_FIELDS},
        "core_status_surfaces": {field: "" for field in UX_CORE_STATUS_SURFACES_FIELDS},
        "entity_affordances": {field: "" for field in UX_ENTITY_AFFORDANCES_FIELDS},
        "action_feedback_matrix": [],
        "progression_legibility": {field: "" for field in UX_PROGRESSION_LEGIBILITY_FIELDS},
        "hud_and_ui_flow": {field: "" for field in UX_HUD_AND_UI_FLOW_FIELDS},
        "visual_language_constraints": {field: "" for field in UX_VISUAL_LANGUAGE_CONSTRAINTS_FIELDS},
        "accessibility_baseline": {field: "" for field in UX_ACCESSIBILITY_BASELINE_FIELDS},
        "acceptance_tests": {field: [] for field in UX_ACCEPTANCE_TESTS_FIELDS},
    }


def empty_value(entry: FieldRegistryEntry) -> Any:
    if entry.value_type == "string_array":
        return []
    if entry.value_type == "tech_stack":
        return empty_tech_stack()
    if entry.value_type == "user_experience_requirements":
        return empty_user_experience_requirements()
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


def deliverable_is_user_facing(patch_series_view: object) -> bool:
    """UX applicability of a series' deliverable (brief 34).

    Memento: the org is a general artifact engine — deliverable-kind
    assumptions belong in this DATA field, never hardcoded in validators
    (live kill: body-format spec run, fingerprint c34426f8ea41ffc8,
    2026-07-05). Missing/unknown applicability reads user_facing so the
    stricter original vocabulary rules stay the default.
    """
    view = patch_series_view if isinstance(patch_series_view, Mapping) else {}
    ux = view.get("user_experience_requirements")
    applicability = ux.get("applicability") if isinstance(ux, Mapping) else None
    kind = applicability.get("applicability") if isinstance(applicability, Mapping) else ""
    return kind != "not_user_facing"


def validate_tech_stack(value: object, *, require_choice: bool = True, user_facing: bool = True) -> bool:
    return not explain_tech_stack_violation(value, require_choice=require_choice, user_facing=user_facing)


def explain_tech_stack_violation(value: object, *, require_choice: bool = True, user_facing: bool = True) -> str:
    """Return the violated tech_stack rule as text, or "" when the value is valid.

    Memento: a bare-boolean rejection converts model self-correction into
    permanent_rejection — the retry contract requires the smallest actionable
    invariant (the same law as codex_exec's fingerprinting Memento). Live case
    (pomodoro CLI request, 2026-07-05): grounding honestly returned
    provenance=requester_specified with an empty build_strategy for a
    surface-only pin; the boolean validator collapsed that into a raw dump plus
    downstream C0 empty-string symptoms, codex "fixed" the strings, tech_stack
    stayed wrong, same fingerprint -> permanent_rejection. This function is the
    single source of truth: validate_tech_stack is derived from it, so the rule
    chain and the boolean can never drift.
    """
    if not isinstance(value, dict) or set(value) != set(TECH_STACK_FIELDS):
        return "tech_stack must be an object with exactly these fields: " + ", ".join(TECH_STACK_FIELDS)
    wrong_types = [field for field in TECH_STACK_FIELDS if not isinstance(value[field], str)]
    if wrong_types:
        return "tech_stack fields must be strings: " + ", ".join(wrong_types)
    if value["build_strategy"] not in TECH_STACK_BUILD_STRATEGIES:
        return "build_strategy must be one of: " + ", ".join(repr(item) for item in TECH_STACK_BUILD_STRATEGIES)
    if value["provenance"] not in TECH_STACK_PROVENANCE:
        return "provenance must be one of: " + ", ".join(TECH_STACK_PROVENANCE)
    if value["provenance"] == "unspecified":
        unspecified_choice_fields = ("build_strategy", "engine", "framework", "language", "platform", "rationale")
        filled = [field for field in unspecified_choice_fields if value[field].strip()]
        if filled:
            return (
                "provenance 'unspecified' requires every choice field to be empty; non-empty: " + ", ".join(filled)
            )
        return ""
    if value["build_strategy"] not in TECH_STACK_CONCRETE_BUILD_STRATEGIES:
        return (
            f"provenance '{value['provenance']}' requires a concrete build_strategy ("
            + ", ".join(TECH_STACK_CONCRETE_BUILD_STRATEGIES)
            + "); if the requester pinned only a platform/surface (for example CLI, browser, mobile), leave "
            "tech_stack unspecified (all choice fields empty) and record the surface in "
            "affected_area_platform and constraints_assumptions"
        )
    if require_choice and value["provenance"] != "unspecified" and not value["rationale"].strip():
        return f"provenance '{value['provenance']}' requires a non-empty rationale"
    if value["build_strategy"] == "from_scratch" and not value["rationale"].strip():
        return "build_strategy 'from_scratch' requires a non-empty rationale"
    if value["provenance"] == "ai_deliberated" and _platform_contains_org_verifier_vocabulary(
        value["platform"], user_facing=user_facing
    ):
        # Brief 34: mode-scoped message, same law as the candidate lint (both
        # wordings verified by blank-context correction probes,
        # scratchpad/brief34_probes/out_{uf,nu}.txt).
        if user_facing:
            return (
                "tech_stack.platform must name the user-facing runtime target, not AI Org verification "
                "mechanisms; org verification constraints belong in the constraints tree"
            )
        return (
            "tech_stack.platform must name the deliverable's own target surface (for example git-committed "
            "specification documents, repository docs); AI Org's own verification mechanisms "
            "(functional_check, codex) are not a platform"
        )
    if value["build_strategy"] == "engine_based":
        engine = value["engine"].strip()
        if not engine or _names_browser_standards(engine):
            return "build_strategy 'engine_based' requires a named engine that is not a browser standard"
    return ""


def validate_user_experience_requirements(value: object, *, require_completeness: bool = True) -> bool:
    if not isinstance(value, dict) or set(value) != set(USER_EXPERIENCE_REQUIREMENTS_FIELDS):
        return False
    applicability = value.get("applicability")
    if not isinstance(applicability, dict) or set(applicability) != set(UX_APPLICABILITY_FIELDS):
        return False
    kind = applicability.get("applicability")
    reason = applicability.get("not_user_facing_reason")
    if kind not in USER_EXPERIENCE_APPLICABILITY or not isinstance(reason, str):
        return False
    if kind == "not_user_facing":
        if not reason.strip():
            return False
        if not _validate_ux_shape(value):
            return False
        # Memento: required-all-props artifact tolerance, mirror image of the
        # 2026-07-03 user_facing incident. Structured outputs fill required
        # present-but-mode-irrelevant UX bodies in both directions. If the
        # deliverable is not user-facing, accept those bodies and clear them;
        # the non-empty reason is the complete UX story.
        _clear_user_experience_requirement_bodies(value)
        return True
    if not _validate_ux_shape(value):
        return False
    if not require_completeness:
        # Memento: required-all-props artifact tolerance. Structured outputs may
        # fill mode-irrelevant required fields; accept then clear them.
        applicability["not_user_facing_reason"] = ""
        return True
    if not _validate_user_facing_ux_completeness(value):
        return False
    applicability["not_user_facing_reason"] = ""
    return True


def _clear_user_experience_requirement_bodies(value: Mapping[str, Any]) -> None:
    for field in (
        "experience_identity",
        "presentation_model",
        "core_status_surfaces",
        "entity_affordances",
        "progression_legibility",
        "hud_and_ui_flow",
        "visual_language_constraints",
        "accessibility_baseline",
    ):
        item = value.get(field)
        if isinstance(item, dict):
            for key in item:
                item[key] = ""

    matrix = value.get("action_feedback_matrix")
    if isinstance(matrix, list):
        matrix.clear()

    acceptance = value.get("acceptance_tests")
    if isinstance(acceptance, dict):
        for key in acceptance:
            acceptance[key] = []


def _validate_ux_shape(value: Mapping[str, Any]) -> bool:
    object_fields = {
        "experience_identity": UX_EXPERIENCE_IDENTITY_FIELDS,
        "presentation_model": UX_PRESENTATION_MODEL_FIELDS,
        "core_status_surfaces": UX_CORE_STATUS_SURFACES_FIELDS,
        "entity_affordances": UX_ENTITY_AFFORDANCES_FIELDS,
        "progression_legibility": UX_PROGRESSION_LEGIBILITY_FIELDS,
        "hud_and_ui_flow": UX_HUD_AND_UI_FLOW_FIELDS,
        "visual_language_constraints": UX_VISUAL_LANGUAGE_CONSTRAINTS_FIELDS,
        "accessibility_baseline": UX_ACCESSIBILITY_BASELINE_FIELDS,
    }
    for field, expected_fields in object_fields.items():
        item = value.get(field)
        if not _is_string_object(item, expected_fields):
            return False
    matrix = value.get("action_feedback_matrix")
    if not isinstance(matrix, list):
        return False
    if not all(_is_string_object(item, UX_ACTION_FEEDBACK_FIELDS) for item in matrix):
        return False
    acceptance = value.get("acceptance_tests")
    if not isinstance(acceptance, dict) or set(acceptance) != set(UX_ACCEPTANCE_TESTS_FIELDS):
        return False
    for field in UX_ACCEPTANCE_TESTS_FIELDS:
        checks = acceptance.get(field)
        if not isinstance(checks, list) or not all(isinstance(check, str) for check in checks):
            return False
    return True


def _is_string_object(value: object, fields: tuple[str, ...]) -> bool:
    return isinstance(value, dict) and set(value) == set(fields) and all(
        isinstance(value[field], str) for field in fields
    )


def _validate_user_facing_ux_completeness(value: Mapping[str, Any]) -> bool:
    for field in (
        "experience_identity",
        "presentation_model",
        "core_status_surfaces",
        "entity_affordances",
        "progression_legibility",
        "hud_and_ui_flow",
        "visual_language_constraints",
        "accessibility_baseline",
    ):
        item = value.get(field)
        if not isinstance(item, Mapping):
            return False
        if any(not str(item.get(key, "")).strip() for key in item):
            return False
    matrix = value.get("action_feedback_matrix")
    if not isinstance(matrix, list) or not matrix:
        return False
    for row in matrix:
        if not isinstance(row, Mapping):
            return False
        if not all(str(row.get(field, "")).strip() for field in UX_ACTION_FEEDBACK_FIELDS):
            return False
    acceptance = value.get("acceptance_tests")
    if not isinstance(acceptance, Mapping):
        return False
    for field in UX_ACCEPTANCE_TESTS_FIELDS:
        checks = acceptance.get(field)
        if not isinstance(checks, list) or not checks:
            return False
        if not all(str(check).strip() for check in checks):
            return False
    return True


ORG_INTERNAL_PLATFORM_TOKENS = ("functional_check", "worktree", "codex")
# Brief 34: the subset that is org vocabulary in EVERY deliverable kind.
ORG_INTERNAL_PLATFORM_TOKENS_ALL_DELIVERABLES = ("functional_check", "codex")
BROWSER_STANDARDS_NAMES = (
    "browser",
    "browser standards",
    "web platform",
    "web standards",
    "html css javascript",
    "html/css/javascript",
    "html/css/js",
    "vanilla web",
    "vanilla browser",
)


def _platform_contains_org_verifier_vocabulary(platform: str, *, user_facing: bool = True) -> bool:
    # This lint is intentionally role-scoped to tech_stack.platform. These tokens
    # are AI Org mechanism names, not user-facing runtime targets.
    # Brief 34: token set scoped by deliverable applicability — functional_check
    # and codex are org names in EVERY deliverable; 'worktree' is org leakage
    # only for user-facing products (for repo-docs/spec deliverables it is git
    # vocabulary of the deliverable's own target surface).
    canonical = platform.lower()
    tokens = ORG_INTERNAL_PLATFORM_TOKENS if user_facing else ORG_INTERNAL_PLATFORM_TOKENS_ALL_DELIVERABLES
    return any(token in canonical for token in tokens)


def _names_browser_standards(value: str) -> bool:
    canonical = " ".join(value.lower().replace("/", " ").replace("-", " ").split())
    compact = value.strip().lower()
    return canonical in BROWSER_STANDARDS_NAMES or compact in BROWSER_STANDARDS_NAMES
