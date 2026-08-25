from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import source_compliance as compliance
import verify_release_legal_gate as gate


REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_PATH = REPO_ROOT / compliance.OWNER_PATH
POLICY_PATH = REPO_ROOT / "legal" / "release-policy.json"


def main() -> int:
    owner = compliance.load_owner(OWNER_PATH)
    aria = owner["kits"][0]
    _assert(aria["status"] == "ready" and aria["source_asset"]["sha256"], "aria2 source owner is not sealed")
    gmp = next(item for item in aria["identities"] if item["component_id"] == "gmp")
    _assert(gmp["identity_type"] == "release-archive" and gmp["immutable_ref"] is None, "GMP identity is not source-control neutral")
    _run_owner_mutations(owner)
    _run_ready_gate_matrix(owner)
    print("source compliance and ready-state gate smoke tests passed")
    return 0


def _run_owner_mutations(owner: dict) -> None:
    mutations = (
        ("missing archive hash", lambda value: value["kits"][0]["identities"][0].__setitem__("archive_sha256", None)),
        ("zero archive hash", lambda value: value["kits"][0]["identities"][0].__setitem__("archive_sha256", "0" * 64)),
        ("mutable Git ref", lambda value: value["kits"][0]["identities"][0].__setitem__("immutable_ref", "main")),
        ("fabricated release archive ref", lambda value: next(item for item in value["kits"][0]["identities"] if item["component_id"] == "gmp").__setitem__("immutable_ref", "fake-git-sha")),
        ("ready blocker", lambda value: value["kits"][0]["blockers"].append("synthetic-blocker")),
        ("ready unresolved input", lambda value: value["kits"][0]["identities"][0].__setitem__("resolved", False)),
        ("local path", lambda value: value["kits"][0]["identities"][0].__setitem__("evidence", "C:\\private\\source")),
        ("secret-like data", lambda value: value["kits"][0]["identities"][0].__setitem__("evidence", "ghp_" + "A" * 32)),
    )
    for label, mutation in mutations:
        candidate = copy.deepcopy(owner)
        mutation(candidate)
        try:
            compliance.validate_owner(candidate)
        except compliance.SourceComplianceError:
            continue
        raise AssertionError(f"source owner mutation was accepted: {label}")


def _run_ready_gate_matrix(source_owner: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="source-ready-gate-") as temporary:
        root = Path(temporary)
        assets = root / "assets"
        assets.mkdir()
        owner = copy.deepcopy(source_owner)
        owner["legal_compliance_certified"] = True
        owner["source_availability_certified"] = True
        for kit in owner["kits"]:
            kit["status"] = "ready"
            kit["blockers"] = []
            for identity in kit["identities"]:
                identity["resolved"] = True
            details = _write_synthetic_source_asset(assets, kit)
            kit["source_asset"] = details
        compliance.validate_owner(owner)

        policy = copy.deepcopy(gate.load_policy(POLICY_PATH))
        policy["legal_compliance_certified"] = True
        policy["source_availability_certified"] = True
        policy["release_payload_integrated"] = True
        current = next(item for item in policy["releases"] if item["tag"] == "v1.3.2")
        current["status"] = "ready"
        current["reason_codes"] = []
        gate.validate_policy_document(policy)
        legal = root / "legal.zip"
        _write_zip(legal, {"legal/SYNTHETIC.txt": b"synthetic legal payload\n"})
        state = gate.validate_release_evidence(
            policy,
            current,
            owner,
            source_assets_root=assets,
            legal_payload=legal,
        )
        _assert(state["release_ready"] is True and state["release_blockers"] == [], "complete ready fixture was rejected")
        ready_policy = root / "ready-policy.json"
        ready_owner = root / "ready-owner.json"
        ready_policy.write_bytes(gate.canonical_policy_bytes(policy))
        ready_owner.write_bytes(compliance.canonical_json_bytes(owner))
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts/verify_release_legal_gate.py"),
                "--policy", str(ready_policy),
                "--tag", "v1.3.2",
                "--source-owner", str(ready_owner),
                "--source-assets-root", str(assets),
                "--legal-payload", str(legal),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        _assert(result.returncode == 0, f"ready gate CLI did not return 0: {result.stderr}")
        _assert(result.stdout.strip() == "Release legal gate ready for v1.3.2", "ready gate CLI output changed")

        _expect_gate_error("legal payload absent", policy, current, owner, assets, root / "missing.zip")
        missing = assets / owner["kits"][0]["source_asset"]["filename"]
        original = missing.read_bytes()
        missing.unlink()
        _expect_gate_error("source asset absent", policy, current, owner, assets, legal)
        missing.write_bytes(original)
        missing.write_bytes(original + b"tamper")
        _expect_gate_error("source asset hash mismatch", policy, current, owner, assets, legal)

        one_flip = copy.deepcopy(gate.load_policy(POLICY_PATH))
        one_flip["legal_compliance_certified"] = True
        try:
            gate.validate_policy_document(one_flip)
        except gate.ReleaseLegalGateError:
            pass
        else:
            raise AssertionError("single certification boolean flip produced a valid ready policy")


def _expect_gate_error(label: str, policy: dict, release: dict, owner: dict, assets: Path, legal: Path) -> None:
    try:
        gate.validate_release_evidence(policy, release, owner, source_assets_root=assets, legal_payload=legal)
    except (gate.ReleaseLegalGateError, compliance.SourceComplianceError):
        return
    raise AssertionError(f"ready gate mutation was accepted: {label}")


def _write_synthetic_source_asset(root: Path, kit: dict) -> dict[str, object]:
    notice = f"synthetic {kit['id']} source fixture; not for distribution\n".encode()
    records = [{"name": "NOTICE.txt", "size": len(notice), "sha256": hashlib.sha256(notice).hexdigest(), "role": "notice"}]
    manifest = {
        "schema_version": 1,
        "package_id": kit["id"],
        "release_tag": "v1.3.2",
        "binary_package": kit["binary_package"],
        "source_identities": kit["identities"],
        "files": records,
        "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"],
    }
    manifest_bytes = compliance.canonical_json_bytes(manifest)
    filename = f"Youtube-Downloaderbs-v1.3.2-{kit['id']}-source.zip"
    path = root / filename
    _write_zip(path, {"NOTICE.txt": notice, "SOURCE_MANIFEST.json": manifest_bytes})
    return {
        "filename": filename,
        "size": path.stat().st_size,
        "sha256": compliance.sha256_file(path),
        "entry_count": 2,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, compliance.FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = compliance.FIXED_FILE_MODE
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
