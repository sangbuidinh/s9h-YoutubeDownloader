from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_sbom_generator_feasibility as verifier  # noqa: E402


JSON_PATH = Path("legal/sbom-generator-feasibility.json")
SUPPORT_PATHS = (
    Path("docs/sbom-generator-feasibility.md"),
    Path("legal/release-assurance-policy.json"),
    Path("legal/built-artifact-inventory.json"),
)


@dataclass(frozen=True)
class NegativeCase:
    name: str
    category: str
    message: str
    mutate: Callable[[Path, dict[str, object]], None]


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _make_fixture(parent: Path, name: str, baseline: dict[str, object]) -> Path:
    root = parent / name
    for relative in (JSON_PATH, *SUPPORT_PATHS):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if relative == JSON_PATH:
            destination.write_bytes(_canonical(baseline))
        else:
            shutil.copy2(ROOT / relative, destination)
    return root


def _write_data(root: Path, data: dict[str, object]) -> None:
    (root / JSON_PATH).write_bytes(_canonical(data))


def _data_mutation(callback: Callable[[dict[str, object]], None]) -> Callable[[Path, dict[str, object]], None]:
    def mutate(root: Path, baseline: dict[str, object]) -> None:
        changed = copy.deepcopy(baseline)
        callback(changed)
        if changed == baseline:
            raise AssertionError("mutation did not change feasibility data")
        _write_data(root, changed)

    return mutate


def _raw_mutation(callback: Callable[[bytes], bytes]) -> Callable[[Path, dict[str, object]], None]:
    def mutate(root: Path, baseline: dict[str, object]) -> None:
        del baseline
        path = root / JSON_PATH
        original = path.read_bytes()
        changed = callback(original)
        if changed == original:
            raise AssertionError("mutation did not change feasibility bytes")
        path.write_bytes(changed)

    return mutate


def _delete_json(root: Path, baseline: dict[str, object]) -> None:
    del baseline
    path = root / JSON_PATH
    path.unlink()
    if path.exists():
        raise AssertionError("missing-file mutation did not remove feasibility JSON")


def _duplicate_key(raw: bytes) -> bytes:
    text = raw.decode("utf-8")
    changed = text.replace("{\n", '{\n  "assessment_baseline": "duplicate",\n', 1)
    if changed.count('"assessment_baseline"') != 2:
        raise AssertionError("duplicate-key mutation did not create exactly two keys")
    return changed.encode("utf-8")


def _malformed_json(raw: bytes) -> bytes:
    del raw
    return b'{"assessment_baseline":\n'


def _set_candidate(data: dict[str, object], index: int, key: str, value: object) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[index]
    assert isinstance(candidate, dict)
    candidate[key] = value


def _replace_project_spdx_source(data: dict[str, object], old: str, new: str) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list) and isinstance(candidates[0], dict)
    sources = candidates[0]["official_sources"]
    assert isinstance(sources, list)
    matching = [index for index, source in enumerate(sources) if source == verifier.SPDX_SCHEMA_URL]
    if len(matching) != 1:
        raise AssertionError("expected exactly one canonical SPDX schema source")
    index = matching[0]
    source = sources[index]
    assert isinstance(source, str)
    if source.count(old) != 1:
        raise AssertionError("SPDX source mutation target is not unique")
    changed = source.replace(old, new, 1)
    if changed == source:
        raise AssertionError("SPDX source mutation did not change the source")
    sources[index] = changed
    sources.sort()
    if source in sources or changed not in sources:
        raise AssertionError("SPDX source mutation was not applied")


def _external_sources(data: dict[str, object], index: int, expected_id: str) -> list[str]:
    candidates = data["candidates"]
    if not isinstance(candidates, list) or index >= len(candidates):
        raise AssertionError("external candidate index is invalid")
    candidate = candidates[index]
    if not isinstance(candidate, dict) or candidate.get("id") != expected_id:
        raise AssertionError(f"expected external candidate at index {index}: {expected_id}")
    sources = candidate.get("official_sources")
    if not isinstance(sources, list) or any(not isinstance(source, str) for source in sources):
        raise AssertionError(f"official sources are invalid: {expected_id}")
    return sources


def _replace_external_source(
    data: dict[str, object], index: int, expected_id: str, old: str, new: str
) -> None:
    sources = _external_sources(data, index, expected_id)
    if sources.count(old) != 1 or new in sources:
        raise AssertionError(f"external source replacement target is not unique: {expected_id}")
    sources[sources.index(old)] = new
    sources.sort()
    if old in sources or sources.count(new) != 1:
        raise AssertionError(f"external source replacement was not applied: {expected_id}")


def _remove_external_source(data: dict[str, object], index: int, expected_id: str, source: str) -> None:
    sources = _external_sources(data, index, expected_id)
    if sources.count(source) != 1:
        raise AssertionError(f"external source removal target is not unique: {expected_id}")
    sources.remove(source)
    sources.sort()
    if source in sources:
        raise AssertionError(f"external source removal was not applied: {expected_id}")


def _add_external_source(data: dict[str, object], index: int, expected_id: str, source: str) -> None:
    sources = _external_sources(data, index, expected_id)
    if source in sources:
        raise AssertionError(f"external source addition target already exists: {expected_id}")
    sources.append(source)
    sources.sort()
    if sources.count(source) != 1:
        raise AssertionError(f"external source addition was not applied: {expected_id}")


def _document_replacement(old: str, new: str) -> Callable[[Path, dict[str, object]], None]:
    def mutate(root: Path, baseline: dict[str, object]) -> None:
        del baseline
        path = root / verifier.DOCUMENT_PATH
        original = path.read_bytes()
        text = original.decode("utf-8")
        if text.count(old) != 1:
            raise AssertionError("document mutation target is not unique")
        changed = text.replace(old, new, 1).encode("utf-8")
        if changed == original:
            raise AssertionError("document mutation did not change the copied document")
        path.write_bytes(changed)
        if path.read_bytes() != changed:
            raise AssertionError("document mutation was not applied")

    return mutate


def _set_decision(data: dict[str, object], key: str, value: object) -> None:
    decision = data["decision"]
    assert isinstance(decision, dict)
    decision[key] = value


def _set_target(data: dict[str, object], key: str, value: object) -> None:
    target = data["target"]
    assert isinstance(target, dict)
    target[key] = value


def _remove_candidate(data: dict[str, object]) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list)
    candidates.pop()


def _swap_candidates(data: dict[str, object]) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list)
    candidates[0], candidates[1] = candidates[1], candidates[0]


def _duplicate_candidate_id(data: dict[str, object]) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list)
    assert isinstance(candidates[0], dict) and isinstance(candidates[1], dict)
    candidates[1]["id"] = candidates[0]["id"]


def _insert_mutable_ref(data: dict[str, object]) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list) and isinstance(candidates[1], dict)
    sources = candidates[1]["official_sources"]
    assert isinstance(sources, list)
    sources.append("https://github.com/anchore/syft/blob/main/README.md")
    sources.sort()


def _remove_criterion(data: dict[str, object]) -> None:
    criteria = data["comparison_criteria"]
    assert isinstance(criteria, list)
    criteria.pop()


def _swap_criteria(data: dict[str, object]) -> None:
    criteria = data["comparison_criteria"]
    assert isinstance(criteria, list)
    criteria[0], criteria[1] = criteria[1], criteria[0]


def _unsupported_evidence_state(data: dict[str, object]) -> None:
    candidates = data["candidates"]
    assert isinstance(candidates, list) and isinstance(candidates[0], dict)
    capabilities = candidates[0]["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities[verifier.CRITERIA_IDS[0]] = "claimed"


def _empty_blockers(data: dict[str, object]) -> None:
    data["blockers"] = []


def _remove_required_blocker(data: dict[str, object]) -> None:
    blockers = data["blockers"]
    assert isinstance(blockers, list)
    blockers.remove("production SBOM not generated")


def _set_claim(data: dict[str, object]) -> None:
    claims = data["claims"]
    assert isinstance(claims, dict)
    claims["production_sbom_generated"] = True


def _claim_historical_inventory(data: dict[str, object]) -> None:
    current = data["current_inputs"]
    assert isinstance(current, dict)
    current["historical_executable_inventory_is_current_final_release_evidence"] = True


def _remove_input_class(data: dict[str, object]) -> None:
    current = data["current_inputs"]
    assert isinstance(current, dict)
    boundary = current["required_future_input_boundary"]
    assert isinstance(boundary, list)
    boundary.remove("checksum file")


def _remove_fail_closed_condition(data: dict[str, object]) -> None:
    prototype = data["prototype_contract"]
    assert isinstance(prototype, dict)
    conditions = prototype["fail_closed_conditions"]
    assert isinstance(conditions, list)
    conditions.remove("semantic reconciliation unavailable")


def _insert_private_key(data: dict[str, object]) -> None:
    blockers = data["blockers"]
    assert isinstance(blockers, list)
    blockers.append("-----BEGIN EC PRIVATE KEY-----")
    blockers.sort()


def _insert_user_path(data: dict[str, object]) -> None:
    blockers = data["blockers"]
    assert isinstance(blockers, list)
    blockers.append("C:\\Users\\example\\private\\scanner")
    blockers.sort()


def _change_assurance_policy(root: Path, baseline: dict[str, object]) -> None:
    del baseline
    path = root / verifier.ASSURANCE_POLICY_PATH
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["sbom"]["generator_selected"] = True
    path.write_bytes(_canonical(policy))
    if json.loads(path.read_text(encoding="utf-8"))["sbom"]["generator_selected"] is not True:
        raise AssertionError("assurance-policy mutation did not occur")


def _cases() -> list[NegativeCase]:
    return [
        NegativeCase("missing-feasibility-json", "file-missing", "SBOM generator feasibility JSON is missing", _delete_json),
        NegativeCase("utf8-bom", "bom", "feasibility JSON contains a UTF-8 BOM", _raw_mutation(lambda raw: b"\xef\xbb\xbf" + raw)),
        NegativeCase("crlf", "line-endings", "feasibility JSON must use LF-only line endings", _raw_mutation(lambda raw: raw.replace(b"\n", b"\r\n"))),
        NegativeCase("missing-final-newline", "final-newline", "feasibility JSON must have exactly one final newline", _raw_mutation(lambda raw: raw.rstrip(b"\n"))),
        NegativeCase("double-final-newline", "final-newline", "feasibility JSON must have exactly one final newline", _raw_mutation(lambda raw: raw + b"\n")),
        NegativeCase("malformed-json", "json-syntax", "feasibility document is not strict JSON at line 2", _raw_mutation(_malformed_json)),
        NegativeCase("duplicate-top-level-key", "duplicate-key", "duplicate JSON key: assessment_baseline", _raw_mutation(_duplicate_key)),
        NegativeCase("noncanonical-json", "canonical-json", "feasibility JSON is not canonical two-space sorted JSON", _raw_mutation(lambda raw: json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")),
        NegativeCase("unknown-top-level-key", "top-level-schema", "top-level feasibility fields are invalid", _data_mutation(lambda data: data.__setitem__("unknown", False))),
        NegativeCase("schema-version-changed", "fixed-value", "schema_version must be integer 1", _data_mutation(lambda data: data.__setitem__("schema_version", 2))),
        NegativeCase("document-id-changed", "fixed-value", "fixed feasibility identity changed: document_id", _data_mutation(lambda data: data.__setitem__("document_id", "changed"))),
        NegativeCase("assessment-baseline-changed", "baseline", "fixed feasibility identity changed: assessment_baseline", _data_mutation(lambda data: data.__setitem__("assessment_baseline", "0" * 40))),
        NegativeCase("repository-changed", "fixed-value", "fixed feasibility identity changed: repository", _data_mutation(lambda data: data.__setitem__("repository", "example/changed"))),
        NegativeCase("product-changed", "fixed-value", "fixed feasibility identity changed: product", _data_mutation(lambda data: data.__setitem__("product", "Changed"))),
        NegativeCase("version-changed", "fixed-value", "fixed feasibility identity changed: version", _data_mutation(lambda data: data.__setitem__("version", "9.9.9"))),
        NegativeCase("scope-changed", "fixed-value", "fixed feasibility identity changed: scope", _data_mutation(lambda data: data.__setitem__("scope", "production"))),
        NegativeCase("spdx-format-changed", "target", "SBOM target changed: format", _data_mutation(lambda data: _set_target(data, "format", "CycloneDX"))),
        NegativeCase("spdx-version-changed", "target", "SBOM target changed: spdx_version", _data_mutation(lambda data: _set_target(data, "spdx_version", "SPDX-2.2"))),
        NegativeCase("predicate-type-changed", "target", "SBOM target changed: predicate_type", _data_mutation(lambda data: _set_target(data, "predicate_type", "https://example.invalid/predicate"))),
        NegativeCase("filename-template-changed", "target", "SBOM target changed: filename_template", _data_mutation(lambda data: _set_target(data, "filename_template", "changed.json"))),
        NegativeCase("candidate-missing", "candidate-set", "candidate set is invalid", _data_mutation(_remove_candidate)),
        NegativeCase("candidate-order-changed", "candidate-order", "candidate order is invalid", _data_mutation(_swap_candidates)),
        NegativeCase("duplicate-candidate-id", "candidate-unique", "candidate IDs must be unique", _data_mutation(_duplicate_candidate_id)),
        NegativeCase("external-repository-changed", "candidate-repository", "candidate repository changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "repository", "example/syft"))),
        NegativeCase("mutable-candidate-ref", "mutable-ref", "mutable evidence references are forbidden", _data_mutation(_insert_mutable_ref)),
        NegativeCase("immutable-commit-malformed", "candidate-commit", "candidate immutable commit is malformed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "immutable_commit", "not-a-commit"))),
        NegativeCase("immutable-commit-changed", "candidate-pin", "candidate immutable commit changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "immutable_commit", "0" * 40))),
        NegativeCase("sbom-tool-immutable-commit-changed", "candidate-pin", "candidate immutable commit changed: microsoft-sbom-tool", _data_mutation(lambda data: _set_candidate(data, 2, "immutable_commit", "d83b43dee2dd70b4d6ba16a97cde6b43f971d9c3"))),
        NegativeCase("syft-official-source-replaced", "candidate-sources", "candidate official sources changed: anchore-syft", _data_mutation(lambda data: _replace_external_source(data, 1, "anchore-syft", "https://github.com/anchore/syft/blob/3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6/schema/spdx-json/spdx-schema-2.3.json", "https://github.com/spdx/spdx-spec/blob/aadf3b0b8dbbabdb4d880b0fc714255fea436ff7/schemas/spdx-schema.json"))),
        NegativeCase("syft-official-source-removed", "candidate-sources", "candidate official sources changed: anchore-syft", _data_mutation(lambda data: _remove_external_source(data, 1, "anchore-syft", "https://github.com/anchore/syft/blob/3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6/README.md"))),
        NegativeCase("syft-official-source-added", "candidate-sources", "candidate official sources changed: anchore-syft", _data_mutation(lambda data: _add_external_source(data, 1, "anchore-syft", "https://github.com/anchore/syft/blob/3e2bc6ed095f7ec1a415fb38cfe1c319e95dfed6/go.mod"))),
        NegativeCase("sbom-tool-official-source-replaced", "candidate-sources", "candidate official sources changed: microsoft-sbom-tool", _data_mutation(lambda data: _replace_external_source(data, 2, "microsoft-sbom-tool", "https://github.com/microsoft/sbom-tool/blob/c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3/README.md", "https://github.com/microsoft/sbom-tool/tree/c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3"))),
        NegativeCase("sbom-tool-official-source-removed", "candidate-sources", "candidate official sources changed: microsoft-sbom-tool", _data_mutation(lambda data: _remove_external_source(data, 2, "microsoft-sbom-tool", "https://github.com/microsoft/sbom-tool/blob/c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3/README.md"))),
        NegativeCase("sbom-tool-official-source-added", "candidate-sources", "candidate official sources changed: microsoft-sbom-tool", _data_mutation(lambda data: _add_external_source(data, 2, "microsoft-sbom-tool", "https://github.com/microsoft/sbom-tool/tree/c83b43dee2dd70b4d6ba16a97cde6b43f971d9c3"))),
        NegativeCase("spdx-commit-malformed", "spdx-evidence-commit", "SPDX specification commit is malformed", _data_mutation(lambda data: _replace_project_spdx_source(data, verifier.SPDX_SPEC_COMMIT, "not-a-commit"))),
        NegativeCase("spdx-commit-changed", "spdx-evidence-pin", "SPDX specification commit changed", _data_mutation(lambda data: _replace_project_spdx_source(data, verifier.SPDX_SPEC_COMMIT, "badf3b0b8dbbabdb4d880b0fc714255fea436ff7"))),
        NegativeCase("spdx-schema-path-changed", "spdx-evidence-path", "SPDX schema path changed", _data_mutation(lambda data: _replace_project_spdx_source(data, verifier.SPDX_SCHEMA_PATH, "schemas/changed-schema.json"))),
        NegativeCase("spdx-release-marker-changed", "spdx-release-pin", "SPDX specification release changed", _document_replacement("spdx/spdx-spec` tag `v2.3`", "spdx/spdx-spec` tag `v2.4`")),
        NegativeCase("spdx-schema-blob-marker-changed", "spdx-schema-pin", "SPDX schema blob changed", _document_replacement(verifier.SPDX_SCHEMA_BLOB_SHA1, "fe61e6686e885f8139c132647fd0b4f483b8fb81")),
        NegativeCase("candidate-release-changed", "candidate-release", "candidate release changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "candidate_release", "v0.0.0"))),
        NegativeCase("license-path-changed", "candidate-license", "candidate license path changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "license_path", "COPYING"))),
        NegativeCase("license-blob-sha-changed", "candidate-license", "candidate license blob SHA changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "license_blob_sha1", "0" * 40))),
        NegativeCase("execution-status-executed", "execution-status", "external candidate execution status changed: anchore-syft", _data_mutation(lambda data: _set_candidate(data, 1, "execution_status", "executed"))),
        NegativeCase("criterion-removed", "criteria-set", "comparison criteria set is invalid", _data_mutation(_remove_criterion)),
        NegativeCase("criterion-order-changed", "criteria-order", "comparison criterion order is invalid", _data_mutation(_swap_criteria)),
        NegativeCase("unsupported-evidence-state", "evidence-state", "unsupported capability evidence state", _data_mutation(_unsupported_evidence_state)),
        NegativeCase("primary-generator-changed", "decision", "primary generator changed", _data_mutation(lambda data: _set_decision(data, "primary_generator", "anchore-syft"))),
        NegativeCase("selected-comparator-changed", "decision", "selected comparator changed", _data_mutation(lambda data: _set_decision(data, "selected_comparator", "microsoft-sbom-tool"))),
        NegativeCase("production-generator-selected", "authorization", "decision authorization must remain false: production_generator_selected", _data_mutation(lambda data: _set_decision(data, "production_generator_selected", True))),
        NegativeCase("prototype-implementation-authorized", "authorization", "decision authorization must remain false: prototype_implementation_authorized", _data_mutation(lambda data: _set_decision(data, "prototype_implementation_authorized", True))),
        NegativeCase("external-comparator-authorized", "authorization", "decision authorization must remain false: external_comparator_execution_authorized", _data_mutation(lambda data: _set_decision(data, "external_comparator_execution_authorized", True))),
        NegativeCase("production-generation-authorized", "authorization", "decision authorization must remain false: production_sbom_generation_authorized", _data_mutation(lambda data: _set_decision(data, "production_sbom_generation_authorized", True))),
        NegativeCase("release-integration-authorized", "authorization", "decision authorization must remain false: release_integration_authorized", _data_mutation(lambda data: _set_decision(data, "release_integration_authorized", True))),
        NegativeCase("blockers-emptied", "blockers", "blockers must not be empty", _data_mutation(_empty_blockers)),
        NegativeCase("required-blocker-removed", "blockers", "required blockers are missing or unordered", _data_mutation(_remove_required_blocker)),
        NegativeCase("claim-set-true", "claims", "claim must remain false: production_sbom_generated", _data_mutation(_set_claim)),
        NegativeCase("historical-inventory-claimed-final", "historical-inventory", "historical executable inventory must not be claimed as final", _data_mutation(_claim_historical_inventory)),
        NegativeCase("required-input-class-removed", "current-inputs", "future input boundary is incomplete", _data_mutation(_remove_input_class)),
        NegativeCase("fail-closed-condition-removed", "fail-closed", "prototype fail-closed conditions changed", _data_mutation(_remove_fail_closed_condition)),
        NegativeCase("private-key-material", "private-key-material", "private-key PEM material is forbidden", _data_mutation(_insert_private_key)),
        NegativeCase("local-user-profile-path", "user-path", "local user-profile paths are forbidden", _data_mutation(_insert_user_path)),
        NegativeCase("assurance-generator-selected", "assurance-policy", "existing assurance policy SBOM state must remain false", _change_assurance_policy),
    ]


def main() -> int:
    baseline = json.loads((ROOT / JSON_PATH).read_text(encoding="utf-8"))
    verifier.verify_feasibility_file(ROOT)
    positive_count = 1
    cases = _cases()
    if len(cases) != 62:
        raise AssertionError(f"expected 62 negative cases, found {len(cases)}")

    with tempfile.TemporaryDirectory(prefix="sbom-feasibility-smoke-") as temporary:
        temp_root = Path(temporary)
        round_trip = _make_fixture(temp_root, "positive-round-trip", baseline)
        verifier.verify_feasibility_file(round_trip)
        positive_count += 1

        for index, case in enumerate(cases, start=1):
            fixture = _make_fixture(temp_root, f"negative-{index:02d}-{case.name}", baseline)
            case.mutate(fixture, baseline)
            try:
                verifier.verify_feasibility_file(fixture)
            except verifier.FeasibilityError as exc:
                if exc.category != case.category or str(exc) != case.message:
                    raise AssertionError(
                        f"{case.name}: expected [{case.category}] {case.message!r}, "
                        f"got [{exc.category}] {str(exc)!r}"
                    ) from exc
            else:
                raise AssertionError(f"{case.name}: mutation was unexpectedly accepted")

    if positive_count != 2:
        raise AssertionError(f"expected 2 positive cases, found {positive_count}")
    print("SBOM generator feasibility smoke passed: 2 positive, 62 negative")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
