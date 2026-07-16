"""Root-owned spine artifact derivation for asset-bearing patch seriess."""
from __future__ import annotations

from typing import Any, Mapping

from ai_org import engineering_precedent_store


ART_BIBLE_PATH = "spine/art-bible.json"
ASSET_MANIFEST_SCHEMA_PATH = "spine/asset-manifest.schema.json"

REFERENCE_TERMS = (
    "art bible two layers plus exemplar anchor",
    "commission contract field union",
    "deformation information redistribution",
    "identity versus commodity asset sourcing",
    "mascot identity system",
    "identity color economy",
)


def assets_warranted(patch_series_view: Mapping[str, Any]) -> bool:
    """Return whether the patch series view describes a user-visible surface needing assets.

    The decision is derived from the structured UX contract. It intentionally
    avoids matching product words in titles or request types; grounding owns
    classifying user-facing applicability and declaring visual/media surfaces.
    """
    ux = patch_series_view.get("user_experience_requirements")
    if not isinstance(ux, Mapping):
        return False
    applicability = ux.get("applicability")
    if not isinstance(applicability, Mapping) or applicability.get("applicability") != "user_facing":
        return False
    signal_sections = (
        "experience_identity",
        "presentation_model",
        "entity_affordances",
        "visual_language_constraints",
    )
    return any(_mapping_has_text(ux.get(section)) for section in signal_sections)


def derive_artifacts(
    patch_series_view: Mapping[str, Any],
    technical_approach: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build root spine artifacts from the patch series view and formed approach."""
    if not assets_warranted(patch_series_view):
        return {}
    citations = _precedent_citations(context or {})
    art_bible = build_art_bible(patch_series_view, technical_approach, citations)
    manifest_schema = build_asset_manifest_schema(citations)
    return {
        ART_BIBLE_PATH: art_bible,
        ASSET_MANIFEST_SCHEMA_PATH: manifest_schema,
    }


def build_art_bible(
    patch_series_view: Mapping[str, Any],
    technical_approach: Mapping[str, Any],
    citations: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    ux = patch_series_view.get("user_experience_requirements") if isinstance(patch_series_view, Mapping) else {}
    ux = ux if isinstance(ux, Mapping) else {}
    identity = _mapping(ux.get("experience_identity"))
    presentation = _mapping(ux.get("presentation_model"))
    visual = _mapping(ux.get("visual_language_constraints"))
    affordances = _mapping(ux.get("entity_affordances"))
    domain = _domain_specification(technical_approach)
    source_citations = list(citations or [])

    return {
        "schema": "ai-org-spine-art-bible-v1",
        "generated_from": {
            "patch_series_fields": ["user_experience_requirements", "background_facts", "proposal_hint"],
            "technical_approach_fields": ["problem.question.decision.implementation.domain_specification"],
        },
        "reference_citations": source_citations,
        "judgment_layer": {
            "visual_pillars": _non_empty_list(
                [
                    identity.get("named_reference"),
                    identity.get("genre_conventions"),
                    visual.get("silhouette_readability"),
                    affordances.get("interactive_entities"),
                ],
                fallback="Readable identity-bearing assets that expose consequential state.",
            ),
            "reference_notes": _precedent_notes(source_citations),
            "negative_examples": _non_empty_list(
                [
                    identity.get("must_not_resemble"),
                    affordances.get("decorative_elements"),
                    visual.get("color_independence"),
                ],
                fallback="Do not ship decorative assets that hide, contradict, or imitate state.",
            ),
            "tone": _first_text(
                identity.get("genre_conventions"),
                patch_series_view.get("background_facts"),
                patch_series_view.get("proposal_hint"),
                default="Clear, legible, and state-forward.",
            ),
            "exemplar_anchor": _first_text(
                identity.get("named_reference"),
                patch_series_view.get("references"),
                default="Grounded patch series identity and source material.",
            ),
        },
        "machine_layer": {
            "palette": {
                "families": _palette_families(visual),
                "hue_budget": {
                    "dominant_hues": 3,
                    "accent_hues": 2,
                    "state_hues_reserved": ["danger", "success", "locked", "available"],
                    "rule": "Use restrained identity colors; do not make color the only state channel.",
                },
            },
            "outline_convention": _first_text(
                visual.get("contrast"),
                visual.get("silhouette_readability"),
                default="Readable outer contour first; internal detail stays subordinate.",
            ),
            "lighting_rule": "Single consistent key light with value staging reserved for identity and state.",
            "proportion_systems_table": [
                {
                    "system": "portrait",
                    "head_count": "3.5",
                    "use": "Dialog, hero, mascot, or inspection-scale assets.",
                    "deformation_rules": ["enlarge identity features", "preserve silhouette", "compress limbs", "elide micro-detail"],
                },
                {
                    "system": "field",
                    "head_count": "2.0",
                    "use": "Map, sprite, token, tile, or playfield-scale assets.",
                    "deformation_rules": ["enlarge face/prop read", "preserve palette identity", "compress torso/limbs", "elide texture"],
                },
            ],
            "deformation_redistribution_rules": {
                "enlarge": ["face read", "signature prop", "state marker"],
                "compress": ["limb length", "surface detail", "secondary costume bands"],
                "preserve": ["silhouette", "palette family", "interaction affordance", "pivot meaning"],
                "elide": ["texture noise", "non-state decoration", "tiny labels"],
            },
            "grid_tile_constraints": _grid_tile_constraints(presentation, affordances, domain),
        },
    }


def build_asset_manifest_schema(citations: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "ai-org-spine-asset-manifest-record-v1",
        "title": "AI Org delivered asset manifest record",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "asset_id",
            "category",
            "kind",
            "dimensions",
            "sheet_layout",
            "state_direction_matrix",
            "pivot",
            "palette_family_ref",
            "license_provenance",
            "acceptance_checks",
        ],
        "properties": {
            "asset_id": {"type": "string"},
            "category": {"type": "string"},
            "kind": {"type": "string"},
            "dimensions": {
                "type": "object",
                "additionalProperties": False,
                "required": ["width", "height", "unit"],
                "properties": {
                    "width": {"type": "integer", "minimum": 1},
                    "height": {"type": "integer", "minimum": 1},
                    "unit": {"type": "string"},
                },
            },
            "sheet_layout": {
                "type": "object",
                "additionalProperties": False,
                "required": ["frame_width", "frame_height", "columns", "rows", "spacing"],
                "properties": {
                    "frame_width": {"type": "integer", "minimum": 1},
                    "frame_height": {"type": "integer", "minimum": 1},
                    "columns": {"type": "integer", "minimum": 1},
                    "rows": {"type": "integer", "minimum": 1},
                    "spacing": {"type": "integer", "minimum": 0},
                },
            },
            "state_direction_matrix": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["state", "directions"],
                    "properties": {
                        "state": {"type": "string"},
                        "directions": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                    },
                },
            },
            "pivot": {
                "type": "object",
                "additionalProperties": False,
                "required": ["x", "y", "origin"],
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "origin": {"type": "string"},
                },
            },
            "palette_family_ref": {"type": "string"},
            "license_provenance": {
                "type": "object",
                "additionalProperties": False,
                "required": ["author", "license", "source"],
                "properties": {
                    "author": {"type": "string"},
                    "license": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
            "acceptance_checks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
        "x-ai-org-source-citations": list(citations or []),
    }


def _precedent_citations(context: Mapping[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for term in REFERENCE_TERMS:
        try:
            lookup = engineering_precedent_store.lookup(term, context, kind="design")
        except Exception as exc:
            citations.append({"term": term, "source": "engineering_precedent_store.lookup", "note": f"lookup failed: {exc}"})
            continue
        candidates = lookup.get("candidates") if isinstance(lookup, Mapping) else []
        candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping) else {}
        citations.append(
            {
                "term": term,
                "source": str(candidate.get("source_url") or candidate.get("evidence") or "engineering_precedent_store.lookup"),
                "note": _first_text(
                    candidate.get("delta_claim"),
                    candidate.get("structure"),
                    candidate.get("summary"),
                    default="No stored candidate returned; term remains cited as a required precedent-store lookup.",
                ),
            }
        )
    return citations


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_has_text(value: Any) -> bool:
    return isinstance(value, Mapping) and any(isinstance(item, str) and bool(item.strip()) for item in value.values())


def _non_empty_list(values: list[Any], *, fallback: str) -> list[str]:
    cleaned = [str(value).strip() for value in values if isinstance(value, str) and value.strip()]
    return cleaned or [fallback]


def _first_text(*values: Any, default: str) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return default


def _precedent_notes(citations: list[dict[str, str]]) -> list[dict[str, str]]:
    notes = []
    for citation in citations:
        notes.append(
            {
                "term": citation.get("term", ""),
                "source": citation.get("source", ""),
                "application": citation.get("note", ""),
            }
        )
    return notes


def _palette_families(visual: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "family_id": "identity",
            "role": _first_text(visual.get("palette_role"), default="Primary recognition and faction/style identity."),
        },
        {
            "family_id": "state",
            "role": "Observable interaction and progression state; always paired with shape, label, or motion.",
        },
    ]


def _grid_tile_constraints(
    presentation: Mapping[str, Any],
    affordances: Mapping[str, Any],
    domain: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "camera_and_view": _first_text(presentation.get("camera_and_view"), default="Use a consistent view system."),
        "world_readability": _first_text(presentation.get("world_readability"), default="Assets must read at gameplay scale."),
        "interactive_entities": _first_text(affordances.get("interactive_entities"), default="Interactive assets require visible affordances."),
        "domain_trace": {
            "aspect_count": len(domain.get("aspects", [])) if isinstance(domain.get("aspects"), list) else 0,
        },
    }


def _domain_specification(technical_approach: Mapping[str, Any]) -> Mapping[str, Any]:
    current: Any = technical_approach
    for part in ("problem", "question", "decision", "implementation", "domain_specification"):
        if not isinstance(current, Mapping):
            return {}
        current = current.get(part)
    return current if isinstance(current, Mapping) else {}
