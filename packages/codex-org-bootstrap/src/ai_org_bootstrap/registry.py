from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class RegistryEntry:
    agent_id: str
    role: str
    adapter: str
    schema: str
    output_to: str | None
    write_scope: tuple[str, ...]


def _value(line: str) -> str:
    return line.split(":", 1)[1].strip().strip('"')


def load_runtime_registry(path: str | Path) -> list[RegistryEntry]:
    """Load the small YAML subset used by registry/runtime-registry.yaml."""
    text = Path(path).read_text(encoding="utf-8")
    entries: list[RegistryEntry] = []
    current: dict[str, object] | None = None
    in_agents = False
    list_key: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "agents:":
            in_agents = True
            continue
        if not in_agents:
            continue
        if re.match(r"^  [A-Za-z0-9_.-]+:$", line):
            if current is not None:
                entries.append(_entry(current))
            current = {"agent_id": stripped[:-1], "write_scope": []}
            list_key = None
            continue
        if current is None:
            continue
        if stripped in {"write_scope:"}:
            list_key = stripped[:-1]
            current[list_key] = []
            continue
        if stripped.startswith("- ") and list_key:
            values = current.setdefault(list_key, [])
            assert isinstance(values, list)
            values.append(stripped[2:].strip().strip('"'))
            continue
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            selected = value.strip()
            if selected == "[]":
                current[key] = []
            else:
                current[key] = selected.strip('"') or None
            list_key = None

    if current is not None:
        entries.append(_entry(current))
    return entries


def _entry(raw: dict[str, object]) -> RegistryEntry:
    def req(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"registry entry {raw.get('agent_id')} missing {key}")
        return value

    write_scope = raw.get("write_scope", [])
    if not isinstance(write_scope, list) or not all(isinstance(item, str) for item in write_scope):
        raise ValueError(f"registry entry {raw.get('agent_id')} has invalid write_scope")
    output_to = raw.get("output_to")
    return RegistryEntry(
        agent_id=req("agent_id"),
        role=req("role"),
        adapter=req("adapter"),
        schema=req("schema"),
        output_to=output_to if isinstance(output_to, str) and output_to else None,
        write_scope=tuple(write_scope),
    )
