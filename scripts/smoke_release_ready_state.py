from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path

import prepare_release_bundle as bundle
import prepare_release_legal_payload as legal_builder
import smoke_source_compliance as source_fixture
import source_compliance
import verify_release_legal_gate as gate
import verify_release_legal_payload as legal_verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
TAG = "v1.3.2"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="release-ready-state-") as temporary:
        root = Path(temporary)
        control = root / "control"
        release = root / "release"
        sources = release / "source-assets"
        assets = release / "assets"
        sources.mkdir(parents=True)
        assets.mkdir()
        _write_ready_control(control, sources)
        exe = assets / bundle.EXE_NAME
        exe.write_bytes(b"MZsynthetic ready-state fixture\n")
        portable = assets / f"Youtube-Downloaderbs-{TAG}.zip"
        legal_builder._write_deterministic_zip(
            portable,
            {"app/SYNTHETIC.txt": b"synthetic ready-state fixture; not for distribution\n"},
            exclusive=True,
        )
        notes = release / bundle.NOTES_NAME
        notes.write_text(
            "# Synthetic ready release\n\n"
            f"- `{portable.name}`: `{legal_verifier.sha256_file(portable)}`\n",
            encoding="utf-8",
            newline="\n",
        )
        legal = assets / f"Youtube-Downloaderbs-{TAG}-legal.zip"
        legal_builder.create_release_legal_payload(
            control_root=control,
            portable_zip=portable,
            output_zip=legal,
            release_notes=notes,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
        )
        legal_manifest = legal_verifier.verify_release_legal_payload(
            control_root=control,
            portable_zip=portable,
            legal_zip=legal,
            release_notes=notes,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
        )
        _assert(legal_manifest["source_kits_ready"] is True, "ready legal payload did not record source readiness")
        output = release / "publish-bundle"
        bundle.create_bundle(
            release_root=release,
            bundle_root=output,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=False,
            policy=control / "legal/release-policy.json",
            asset_contract=control / "legal/release-assets-v2.json",
            legal_payload_path=legal,
            source_assets_root=sources,
        )
        bundle.verify_bundle(
            bundle_root=output,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=False,
            policy=control / "legal/release-policy.json",
            asset_contract=control / "legal/release-assets-v2.json",
            legal_payload_path=output / "assets" / legal.name,
            source_assets_root=output / "assets",
            require_release_ready=True,
        )
        manifest = json.loads((output / bundle.MANIFEST_NAME).read_text(encoding="utf-8"))
        _assert(manifest["release_ready"] is True, "ready bundle manifest is not ready")
        _assert(manifest["legal_compliance_certified"] is True, "ready bundle legal state is false")
        _assert(manifest["source_availability_certified"] is True, "ready bundle source state is false")
        _assert(manifest["release_blockers"] == [], "ready bundle contains blockers")
    print("release ready-state legal payload and bundle smoke tests passed")
    return 0


def _write_ready_control(control: Path, sources: Path) -> None:
    contract = json.loads((REPO_ROOT / "legal/release-assets-v2.json").read_text(encoding="utf-8"))
    contract["release_readiness"] = "ready"
    contract["legal_compliance_certified"] = True
    contract["source_availability_certified"] = True
    contract["source_kits_ready"] = True
    for item in contract["required_source_asset_templates"]:
        item["status"] = "ready"
    contract["release_blockers"] = []

    owner = copy.deepcopy(source_compliance.load_owner(REPO_ROOT / source_compliance.OWNER_PATH))
    owner["legal_compliance_certified"] = True
    owner["source_availability_certified"] = True
    for kit in owner["kits"]:
        kit["status"] = "ready"
        kit["blockers"] = []
        for identity in kit["identities"]:
            identity["resolved"] = True
        kit["source_asset"] = source_fixture._write_synthetic_source_asset(sources, kit)
    source_compliance.validate_owner(owner)

    policy = copy.deepcopy(gate.load_policy(REPO_ROOT / "legal/release-policy.json"))
    policy["legal_compliance_certified"] = True
    policy["source_availability_certified"] = True
    policy["release_payload_integrated"] = True
    release = next(item for item in policy["releases"] if item["tag"] == TAG)
    release["status"] = "ready"
    release["reason_codes"] = []
    gate.validate_policy_document(policy)

    for relative in legal_verifier.LEGAL_PAYLOAD_FILES:
        source = REPO_ROOT / relative
        destination = control / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    (control / "legal/release-assets-v2.json").write_bytes(source_compliance.canonical_json_bytes(contract))
    (control / "legal/release-policy.json").write_bytes(gate.canonical_policy_bytes(policy))
    (control / source_compliance.OWNER_PATH).write_bytes(source_compliance.canonical_json_bytes(owner))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
