from __future__ import annotations

import copy
import json
from pathlib import Path

import inventory_built_executable as inventory_tool


REPO_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPO_ROOT / "legal" / "built-artifact-inventory.json"


def main() -> int:
    inventory = inventory_tool.load_inventory(INVENTORY_PATH)
    _assert(
        INVENTORY_PATH.read_bytes() == inventory_tool.canonical_inventory_bytes(inventory),
        "inventory serialization is not deterministic",
    )
    _assert(inventory["source_commit"] == inventory_tool.BASELINE_COMMIT, "source commit changed")
    names = [record["name"] for record in inventory["native_members"]]
    _assert(names == sorted(names, key=lambda value: (value.casefold(), value)), "native members are unsorted")
    _assert(all(record["size"] > 0 for record in inventory["native_members"]), "native size is invalid")
    _assert(any(record["status"] == "identified" for record in inventory["native_members"]), "identified state is absent")
    _assert(bool(inventory["unresolved_native_members"]), "unresolved state is absent")
    for relative in ("README.md", "THIRD_PARTY_NOTICES.md", "legal/README.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        _assert("legal/built-artifact-inventory.json" in text, f"inventory is undocumented in {relative}")
    _run_mutations(inventory)
    print("built artifact inventory smoke tests passed")
    return 0


def _run_mutations(source: dict) -> None:
    mutations = (
        ("wrong schema", lambda value: value.__setitem__("schema_version", 2)),
        ("wrong source commit", lambda value: value.__setitem__("source_commit", "0" * 40)),
        ("wrong platform", lambda value: value.__setitem__("target_platform", "linux-x86_64")),
        ("wrong Python version", lambda value: value.__setitem__("python_version", "3.12.0")),
        ("wrong PyInstaller version", lambda value: value.__setitem__("pyinstaller_version", "6.20.0")),
        ("duplicate native member", _duplicate_native),
        ("unsorted native member", _unsort_native),
        ("invalid SHA-256", lambda value: value["native_members"][0].__setitem__("sha256", "x" * 64)),
        ("zero size", lambda value: value["native_members"][0].__setitem__("size", 0)),
        ("absolute member path", lambda value: value["native_members"][0].__setitem__("name", "C:/unsafe.dll")),
        ("traversal member", lambda value: value["native_members"][0].__setitem__("name", "../unsafe.dll")),
        ("backslash ambiguity", lambda value: value["native_members"][0].__setitem__("name", "dir\\unsafe.dll")),
        ("guessed version without evidence", _guess_version),
        ("unresolved member omitted", lambda value: value["unresolved_native_members"].pop()),
        (
            "local path",
            lambda value: _add_component_evidence(value, "C:" + "\\Users\\developer\\binary.dll"),
        ),
        ("timestamp field", lambda value: value.__setitem__("timestamp", "2026-07-14T00:00:00Z")),
        ("exhaustive-inventory claim", lambda value: _add_component_evidence(value, "exhaustive inventory")),
        ("legal compliance claim", lambda value: _add_component_evidence(value, "legal compliance")),
        ("EXE hash malformed", lambda value: value["executable"].__setitem__("sha256", "0" * 63)),
        (
            "canonical list digest malformed",
            lambda value: value["archive"].__setitem__("canonical_member_list_sha256", "0" * 63),
        ),
    )
    for label, mutation in mutations:
        candidate = copy.deepcopy(source)
        mutation(candidate)
        try:
            inventory_tool.validate_inventory_document(candidate)
        except inventory_tool.InventoryError:
            continue
        raise AssertionError(f"inventory mutation was accepted: {label}")


def _duplicate_native(value: dict) -> None:
    value["native_members"].append(copy.deepcopy(value["native_members"][0]))
    value["archive"]["native_member_count"] += 1


def _unsort_native(value: dict) -> None:
    value["native_members"][0], value["native_members"][1] = (
        value["native_members"][1],
        value["native_members"][0],
    )


def _guess_version(value: dict) -> None:
    record = next(component for component in value["detected_components"] if component["version"] == "unverified")
    record["version"] = "9.9.9"
    record["evidence"] = []


def _add_component_evidence(value: dict, evidence: str) -> None:
    record = value["detected_components"][0]
    record["evidence"].append(evidence)
    record["evidence"].sort(key=str.casefold)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
