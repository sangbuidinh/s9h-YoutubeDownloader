from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import verify_release_legal_gate as gate


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "legal" / "release-policy.json"
GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_release_legal_gate.py"


def main() -> int:
    policy = gate.load_policy(POLICY_PATH)
    _assert(tuple(release["tag"] for release in policy["releases"]) == gate.EXPECTED_TAGS, "tag set changed")
    _assert(all(release["status"] == "blocked" for release in policy["releases"][:-1]), "a historical tag is not blocked")
    _assert("bypass" not in json.dumps(policy).casefold(), "bypass field exists")
    for tag in gate.EXPECTED_TAGS:
        release = gate.release_for_tag(policy, tag)
        if release["status"] in {"technical-ready", "authorized-ready"}:
            state = gate.validate_repository_control(REPO_ROOT)
            _assert(state["technical_source_ready"] and not state["release_ready"], "technical state is not fail-closed")
            result = subprocess.run([sys.executable, str(GATE_SCRIPT), "--policy", str(POLICY_PATH), "--tag", tag], capture_output=True, text=True)
            _assert(result.returncode == 2 and "ready source assets root is required" in result.stderr, "final gate accepted missing evidence")
            continue
        expected = f"Release legal gate blocked for {tag}: {', '.join(release['reason_codes'])}"
        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT), "--policy", str(POLICY_PATH), "--tag", tag],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        _assert(result.returncode == 1, f"gate did not block {tag}")
        _assert(result.stdout.strip() == expected, f"gate output changed for {tag}")
        _assert(not result.stderr.strip(), f"gate wrote stderr for {tag}")
    _run_semantic_mutations(policy)
    _run_byte_mutations(policy)
    print("release legal gate smoke tests passed")
    return 0


def _run_semantic_mutations(source: dict) -> None:
    mutations = (
        ("missing tag", lambda value: value["releases"].pop()),
        ("duplicate tag", _duplicate_tag),
        ("unsorted tags", _unsort_tags),
        ("status allow", lambda value: value["releases"][0].__setitem__("status", "allow")),
        ("status ready", lambda value: value["releases"][0].__setitem__("status", "ready")),
        ("legal compliance mismatch", lambda value: value.__setitem__("legal_compliance_certified", not value["legal_compliance_certified"])),
        ("source availability mismatch", lambda value: value.__setitem__("source_availability_certified", not value["source_availability_certified"])),
        ("release payload integrated true", lambda value: value.__setitem__("release_payload_integrated", True)),
        ("missing FFmpeg reason", lambda value: _remove_reason(value, "v1.3.1", "ffmpeg-corresponding-source-not-certified")),
        ("missing aria2 reason", lambda value: _remove_reason(value, "v1.3.0", "aria2-source-availability-not-integrated")),
        (
            "missing historical-runtime reason",
            lambda value: _remove_reason(value, "v1.2.7-rc.1", "historical-runtime-correspondence-not-certified"),
        ),
        ("unknown field", lambda value: value.__setitem__("unexpected", True)),
        (
            "local path",
            lambda value: value["releases"][0]["reason_codes"].__setitem__(
                0, "C:" + "\\Users\\developer\\policy"
            ),
        ),
        ("secret-like value", lambda value: value["releases"][0]["reason_codes"].__setitem__(0, "ghp_" + "A" * 32)),
        ("environment override", lambda value: value.__setitem__("environment_override", "ALLOW_RELEASE")),
        ("branch allowlist", lambda value: value.__setitem__("branch_allowlist", ["main"])),
        ("user allowlist", lambda value: value.__setitem__("user_allowlist", ["maintainer"])),
        ("expiry date", lambda value: value.__setitem__("expiry", "2026-12-31")),
        ("empty reason list", lambda value: value["releases"][0].__setitem__("reason_codes", [])),
    )
    for label, mutation in mutations:
        candidate = copy.deepcopy(source)
        mutation(candidate)
        try:
            gate.validate_policy_document(candidate)
        except gate.ReleaseLegalGateError:
            continue
        raise AssertionError(f"release policy mutation was accepted: {label}")


def _run_byte_mutations(policy: dict) -> None:
    canonical = gate.canonical_policy_bytes(policy)
    mutations = (
        ("malformed JSON", b"{malformed\n"),
        ("BOM", b"\xef\xbb\xbf" + canonical),
        ("CRLF", canonical.replace(b"\n", b"\r\n")),
    )
    for label, data in mutations:
        with tempfile.TemporaryDirectory(prefix="release-gate-smoke-") as temporary:
            path = Path(temporary) / "policy.json"
            path.write_bytes(data)
            try:
                gate.load_policy(path)
            except (gate.ReleaseLegalGateError, json.JSONDecodeError, UnicodeError):
                continue
            raise AssertionError(f"release policy byte mutation was accepted: {label}")


def _duplicate_tag(value: dict) -> None:
    value["releases"][1]["tag"] = value["releases"][0]["tag"]


def _unsort_tags(value: dict) -> None:
    value["releases"][0], value["releases"][1] = value["releases"][1], value["releases"][0]


def _remove_reason(value: dict, tag: str, reason: str) -> None:
    release = next(item for item in value["releases"] if item["tag"] == tag)
    release["reason_codes"].remove(reason)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
