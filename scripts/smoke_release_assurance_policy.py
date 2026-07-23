from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from verify_release_assurance_policy import POLICY_PATH, PolicyError, verify_policy_file


Mutation = Callable[[dict[str, Any]], None]


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_policy(root: Path, raw: bytes) -> None:
    path = root / POLICY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def _load_repository_policy(root: Path) -> tuple[dict[str, Any], bytes]:
    raw = (root / POLICY_PATH).read_bytes()
    return json.loads(raw.decode("utf-8")), raw


def _run_positive(name: str, root: Path) -> None:
    verify_policy_file(root)
    print(f"PASS positive: {name}")


def _run_negative(
    name: str,
    expected_category: str,
    repository_policy: dict[str, Any],
    repository_raw: bytes,
    *,
    mutation: Mutation | None = None,
    raw_mutation: Callable[[bytes], bytes] | None = None,
    omit_policy: bool = False,
) -> None:
    with tempfile.TemporaryDirectory(prefix="s9h-release-assurance-smoke-") as temporary:
        root = Path(temporary)
        if not omit_policy:
            if mutation is not None:
                value = copy.deepcopy(repository_policy)
                mutation(value)
                raw = _canonical_bytes(value)
            else:
                raw = repository_raw
            if raw_mutation is not None:
                raw = raw_mutation(raw)
            _write_policy(root, raw)
        try:
            verify_policy_file(root)
        except PolicyError as exc:
            if exc.category != expected_category:
                raise AssertionError(
                    f"{name}: expected category {expected_category}, got {exc.category}: {exc}"
                ) from exc
        else:
            raise AssertionError(f"{name}: mutation unexpectedly passed")
    print(f"PASS negative: {name} [{expected_category}]")


def _set(path: tuple[str, ...], value: Any) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: dict[str, Any] = policy
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _delete_list_item(path: tuple[str, ...], value: str) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: Any = policy
        for key in path:
            target = target[key]
        target.remove(value)

    return mutate


def _insert_sorted_list_item(path: tuple[str, ...], value: str) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: Any = policy
        for key in path:
            target = target[key]
        target.append(value)
        target.sort()

    return mutate


def _add(path: tuple[str, ...], key: str, value: Any) -> Mutation:
    def mutate(policy: dict[str, Any]) -> None:
        target: dict[str, Any] = policy
        for part in path:
            target = target[part]
        target[key] = value

    return mutate


def _duplicate_top_key(raw: bytes) -> bytes:
    marker = b'{\n  "assessment_baseline": '
    if not raw.startswith(marker):
        raise AssertionError("canonical policy prefix changed")
    line_end = raw.index(b"\n", len(marker)) + 1
    return raw[:line_end] + raw[len(b"{\n") : line_end] + raw[line_end:]


def _checksum_before_signing(policy: dict[str, Any]) -> None:
    sequence = policy["release_integration"]["sequence"]
    checksum = sequence.pop(5)
    sequence.insert(2, checksum)


def _attestation_before_final_bytes(policy: dict[str, Any]) -> None:
    sequence = policy["release_integration"]["sequence"]
    attestation = sequence.pop(9)
    sequence.insert(1, attestation)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    repository_policy, repository_raw = _load_repository_policy(root)

    _run_positive("repository policy", root)
    with tempfile.TemporaryDirectory(prefix="s9h-release-assurance-positive-") as temporary:
        copied_root = Path(temporary)
        _write_policy(copied_root, _canonical_bytes(repository_policy))
        _run_positive("canonical round-trip copy", copied_root)

    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("missing policy", "file-missing", {"omit_policy": True}),
        ("UTF-8 BOM", "bom", {"raw_mutation": lambda raw: b"\xef\xbb\xbf" + raw}),
        ("CRLF line endings", "line-endings", {"raw_mutation": lambda raw: raw.replace(b"\n", b"\r\n")}),
        ("duplicate JSON key", "duplicate-key", {"raw_mutation": _duplicate_top_key}),
        ("unknown top-level key", "top-level-schema", {"mutation": _add((), "unexpected", False)}),
        ("unknown nested key", "nested-schema", {"mutation": _add(("authenticode",), "unexpected", False)}),
        ("schema version changed", "fixed-value", {"mutation": _set(("schema_version",), 1)}),
        ("baseline commit malformed", "baseline", {"mutation": _set(("assessment_baseline",), "not-a-commit")}),
        ("Authenticode readiness true", "auth-readiness", {"mutation": _set(("authenticode", "readiness"), True)}),
        ("certificate provisioned true", "auth-certificate", {"mutation": _set(("authenticode", "certificate_provisioned"), True)}),
        ("signing target changed", "auth-target", {"mutation": _set(("authenticode", "first_party_signing_targets"), ["vendor.exe"])}),
        ("third-party re-signing enabled", "third-party-boundary", {"mutation": _set(("authenticode", "third_party_resigning", "allowed"), True)}),
        ("SHA1 file digest", "auth-digest", {"mutation": _set(("authenticode", "file_digest"), "SHA1")}),
        ("non-RFC3161 timestamp protocol", "timestamp-protocol", {"mutation": _set(("authenticode", "candidate_timestamp", "protocol"), "legacy")}),
        ("SHA1 timestamp digest", "timestamp-digest", {"mutation": _set(("authenticode", "candidate_timestamp", "digest"), "SHA1")}),
        ("empty Authenticode blockers", "auth-blockers", {"mutation": _set(("authenticode", "blockers"), [])}),
        ("SBOM readiness true", "sbom-readiness", {"mutation": _set(("sbom", "readiness"), True)}),
        ("SBOM format changed", "sbom-format", {"mutation": _set(("sbom", "format"), "CycloneDX")}),
        ("SPDX predicate changed", "sbom-predicate", {"mutation": _set(("sbom", "predicate_type"), "https://example.invalid/predicate")}),
        ("required SBOM scope removed", "sbom-scope", {"mutation": _delete_list_item(("sbom", "coverage_categories"), "aria2")}),
        ("generator selection removed", "sbom-generator", {"mutation": _set(("sbom", "generator_selected"), False)}),
        ("SBOM implementation removed", "sbom-implementation", {"mutation": _set(("sbom", "implementation_status"), False)}),
        ("synthetic integration claim removed", "sbom-implementation", {"mutation": _set(("sbom", "implementation_evidence", "synthetic_integration_validated"), False)}),
        ("production SBOM claim enabled", "sbom-implementation", {"mutation": _set(("sbom", "implementation_evidence", "production_sbom_generated"), True)}),
        ("generator version changed", "sbom-implementation", {"mutation": _set(("sbom", "implementation_evidence", "generator_version"), "9.9.9")}),
        ("empty SBOM blockers", "sbom-blockers", {"mutation": _set(("sbom", "blockers"), [])}),
        ("provenance readiness true", "provenance-readiness", {"mutation": _set(("provenance", "readiness"), True)}),
        ("provenance action changed", "provenance-action", {"mutation": _set(("provenance", "action_repository"), "example/attest")}),
        ("candidate action SHA malformed", "action-pin", {"mutation": _set(("provenance", "candidate_action_commit"), "v4")}),
        ("candidate action commit changed", "action-pin", {"mutation": _set(("provenance", "candidate_action_commit"), "0" * 40)}),
        ("workflow permissions integrated", "workflow-permissions", {"mutation": _set(("provenance", "workflow_permission_review", "integrated"), True)}),
        ("artifact metadata permission removed", "workflow-permissions", {"mutation": _delete_list_item(("provenance", "workflow_permission_review", "required_job_permissions"), "artifact-metadata: write")}),
        ("final subject removed", "provenance-subjects", {"mutation": _delete_list_item(("provenance", "subjects"), "SHA256SUMS.txt")}),
        ("source-kit archive mandatory", "source-kit-subject", {"mutation": _set(("provenance", "source_kit_subjects_mandatory"), True)}),
        ("empty provenance blockers", "provenance-blockers", {"mutation": _set(("provenance", "blockers"), [])}),
        ("final-byte ordering disabled", "final-byte-order", {"mutation": _set(("release_integration", "all_byte_changes_before_finalization"), False)}),
        ("checksum before signing", "final-byte-order", {"mutation": _checksum_before_signing}),
        ("attestation before final byte changes", "final-byte-order", {"mutation": _attestation_before_final_bytes}),
        ("release-assurance readiness true", "integration-readiness", {"mutation": _set(("release_integration", "readiness", "release_assurance_ready"), True)}),
        ("claim set true", "claims", {"mutation": _set(("claims", "sbom_generated"), True)}),
        ("legal/source-kit invariant changed", "gate-invariants", {"mutation": _set(("release_integration", "existing_gate_invariants", "source_kits_ready"), True)}),
        ("PFX path added", "certificate-material", {"mutation": _add(("authenticode",), "pfx_" + "path", "fixtures/" + "identity." + "pfx")}),
        ("certificate password field added", "secret-key", {"mutation": _add(("authenticode",), "certificate_" + "password", "synthetic-fixture")}),
        ("private key material added", "private-key-material", {"mutation": _add(("authenticode",), "key_material", "-----BEGIN " + "PRIVATE" + " KEY-----")}),
        ("EC private-key PEM header", "private-key-material", {"mutation": _insert_sorted_list_item(("authenticode", "blockers"), "-----BEGIN EC PRIVATE KEY-----")}),
        ("encrypted private-key PEM header", "private-key-material", {"mutation": _insert_sorted_list_item(("authenticode", "blockers"), "-----BEGIN ENCRYPTED PRIVATE KEY-----")}),
        ("OpenSSH private-key PEM header", "private-key-material", {"mutation": _insert_sorted_list_item(("authenticode", "blockers"), "-----BEGIN OPENSSH PRIVATE KEY-----")}),
        ("user-profile path added", "user-path", {"mutation": _add(("authenticode",), "notes", "C:" + "\\Users" + "\\example\\signing")}),
        ("unsupported production-ready claim", "unsupported-claim", {"mutation": _add(("authenticode",), "status", "production ready")}),
    ]

    for name, category, options in cases:
        _run_negative(name, category, repository_policy, repository_raw, **options)

    print(f"Release assurance policy smoke passed: 2 positive, {len(cases)} negative")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
