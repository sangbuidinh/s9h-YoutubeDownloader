from __future__ import annotations

import copy
import json
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path

import prepare_release_bundle as bundle
import prepare_release_legal_payload as legal_builder
import smoke_source_compliance as source_fixture
import source_compliance
import verify_release_legal_gate as gate
import verify_release_legal_payload as legal_verifier
import ffmpeg_correspondence
import release_authorization


REPO_ROOT = Path(__file__).resolve().parents[1]
TAG = "v1.3.2"
SOURCE_COMMIT = "1" * 40
CONTROL_COMMIT = "2" * 40


def main() -> int:
    _exercise_ready_state(authorized=True)
    _exercise_ready_state(authorized=False)
    print("release ready-state full-content gate, authorization, and bundle smoke tests passed")
    return 0


def _exercise_ready_state(*, authorized: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="release-ready-state-") as temporary:
        root = Path(temporary)
        control = root / "control"
        release = root / "release"
        sources = release / "source-assets"
        assets = release / "assets"
        sources.mkdir(parents=True)
        assets.mkdir()
        _write_ready_control(control, sources, authorized=authorized)
        policy = gate.load_policy(control / "legal/release-policy.json")
        current = gate.release_for_tag(policy, TAG)
        owner = source_compliance.load_owner(control / source_compliance.OWNER_PATH)
        prebuild = gate.validate_prebuild_control(policy, current, owner, control_root=control)
        _assert(prebuild["technical_source_ready"] is True, "prebuild rejected technical inputs")
        _assert(prebuild["release_payload_integrated"] is False and prebuild["release_ready"] is False,
                "prebuild claimed final integration or publication")
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
        context = dict(control_root=control, source_assets_root=sources, legal_payload=legal,
                       portable_zip=portable, release_notes=notes, source_commit=SOURCE_COMMIT,
                       control_commit=CONTROL_COMMIT)
        state = gate.validate_release_evidence(policy, current, owner, **context)
        _assert(state["release_payload_integrated"] is True and state["release_ready"] is authorized,
                "final evidence did not separate technical readiness from legal authorization")
        _negative_matrix(policy, current, owner, context)
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
        verify_args = dict(
            bundle_root=output,
            tag=TAG,
            source_commit=SOURCE_COMMIT,
            control_commit=CONTROL_COMMIT,
            prerelease=False,
            policy=control / "legal/release-policy.json",
            asset_contract=control / "legal/release-assets-v2.json",
            legal_payload_path=output / "assets" / legal.name,
            source_assets_root=output / "assets",
            require_release_ready=authorized,
        )
        bundle.verify_bundle(**verify_args)
        if not authorized:
            verify_args["require_release_ready"] = True
            _expect_error(lambda: bundle.verify_bundle(**verify_args), "not approved for publishing")
        manifest = json.loads((output / bundle.MANIFEST_NAME).read_text(encoding="utf-8"))
        _assert(manifest["release_ready"] is authorized, "bundle publication state is invalid")
        _assert(manifest["legal_compliance_certified"] is authorized, "bundle legal authorization is invalid")
        _assert(manifest["source_availability_certified"] is True, "ready bundle source state is false")
        _assert(manifest["release_blockers"] == ([] if authorized else ["legal-release-authorization-required"]), "bundle blockers are invalid")


def _write_ready_control(control: Path, sources: Path, *, authorized: bool = True) -> None:
    contract = json.loads((REPO_ROOT / "legal/release-assets-v2.json").read_text(encoding="utf-8"))
    contract["release_readiness"] = "ready" if authorized else "technical-ready"
    contract["legal_compliance_certified"] = authorized
    contract["source_availability_certified"] = True
    contract["source_kits_ready"] = True
    for item in contract["required_source_asset_templates"]:
        item["status"] = "ready"
    contract["release_blockers"] = [] if authorized else ["legal-release-authorization-required"]

    owner = copy.deepcopy(source_compliance.load_owner(REPO_ROOT / source_compliance.OWNER_PATH))
    owner["legal_compliance_certified"] = False
    owner["source_availability_certified"] = True
    correspondence = _synthetic_correspondence(owner)
    for kit in owner["kits"]:
        kit["status"] = "ready"
        kit["blockers"] = []
        for identity in kit["identities"]:
            identity["resolved"] = True
        kit["source_asset"] = source_fixture._write_synthetic_source_asset(sources, kit)
    by_id = {item["component_id"]: item for item in owner["kits"][1]["identities"]}
    for row in correspondence["direct_components"] + correspondence["transitive_components"]:
        if row["id"] in by_id:
            row["source_archive_size"] = by_id[row["id"]]["archive_size"]
            row["source_archive_sha256"] = by_id[row["id"]]["archive_sha256"]
    source_compliance.validate_owner(owner)

    policy = copy.deepcopy(gate.load_policy(REPO_ROOT / "legal/release-policy.json"))
    policy["legal_compliance_certified"] = authorized
    policy["source_availability_certified"] = True
    policy["release_payload_integrated"] = False
    release = next(item for item in policy["releases"] if item["tag"] == TAG)
    release["status"] = "authorized-ready" if authorized else "technical-ready"
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
    (control / ffmpeg_correspondence.RECORD_PATH).write_bytes(source_compliance.canonical_json_bytes(correspondence))
    authorization = json.loads((REPO_ROOT / release_authorization.AUTHORIZATION_PATH).read_text(encoding="utf-8"))
    if authorized:
        authorization.update(state="LEGAL_RELEASE_AUTHORIZED", decision_reference="synthetic-external-review-only",
                             reviewed_source_commit=SOURCE_COMMIT,
                             reviewed_policy_sha256=release_authorization.document_sha256(policy),
                             reviewed_source_owner_sha256=release_authorization.document_sha256(owner),
                             reviewed_asset_contract_sha256=release_authorization.document_sha256(contract),
                             reviewed_ffmpeg_correspondence_sha256=release_authorization.document_sha256(correspondence))
    (control / release_authorization.AUTHORIZATION_PATH).write_bytes(release_authorization.canonical_bytes(authorization))


def _synthetic_correspondence(owner: dict) -> dict:
    value = ffmpeg_correspondence.load_record(REPO_ROOT / ffmpeg_correspondence.RECORD_PATH)
    kit = owner["kits"][1]
    template = copy.deepcopy(kit["identities"][0])
    for row in value["direct_components"] + value["transitive_components"]:
        if row["classification"] != "REQUIRED_FOR_CORRESPONDING_SOURCE":
            continue
        item = copy.deepcopy(template)
        item.update(component_id=row["id"], project="synthetic-source-not-for-distribution",
                    version="synthetic", archive_filename=row["id"] + ".tar.gz", resolved=True)
        kit["identities"].append(item)
        row.update(provider_version="synthetic", resolved=True, immutable_identity_type=item["identity_type"],
                   immutable_ref=item["immutable_ref"], source_archive_filename=item["archive_filename"],
                   source_archive_url=item["archive_url"], source_archive_size=item["archive_size"],
                   source_archive_sha256=item["archive_sha256"],
                   license={"archive_member":"SYNTHETIC-LICENSE", "sha256":"a" * 64, "scope":"synthetic only"})
    kit["identities"].sort(key=lambda item: item["component_id"])
    value.update(transitive_inventory_complete=True, exact_provider_build_snapshot_proven=True,
                 exact_provider_patch_set_proven=True, verdict="COMPLETE")
    for row in value["system_dispositions"]:
        row["complete"] = True
    ffmpeg_correspondence.validate_record(value)
    return value


def _expect_error(action, expected: str = "") -> None:
    try:
        action()
    except (gate.ReleaseLegalGateError, source_compliance.SourceComplianceError,
            legal_verifier.LegalPayloadError, release_authorization.AuthorizationError, bundle.BundleError) as exc:
        _assert(expected in str(exc), f"wrong failure: expected {expected!r}, got {exc}")
        return
    raise AssertionError("negative ready-state fixture was accepted")


def _negative_matrix(policy: dict, current: dict, owner: dict, context: dict) -> None:
    def final():
        return gate.validate_release_evidence(policy, current, owner, **context)

    legal = context["legal_payload"]
    original = legal.read_bytes()
    source_fixture._write_zip(legal, {"arbitrary.txt": b"nonempty ZIP is not legal evidence\n"})
    _expect_error(final)
    legal.write_bytes(original)

    for kit in owner["kits"]:
        manifest = source_compliance.verify_source_asset(owner, kit["id"], context["source_assets_root"] / kit["source_asset"]["filename"])
        entries = source_compliance._read_deterministic_zip(context["source_assets_root"] / kit["source_asset"]["filename"], "fixture")
        missing_archive = next(row["name"] for row in manifest["files"] if row["role"] == "source-archive")
        entries.pop(missing_archive)
        manifest["files"] = [row for row in manifest["files"] if row["name"] != missing_archive]
        _expect_error(lambda: source_compliance._validate_embedded_manifest(manifest, entries, kit), "embedded source archive is missing")
        path = context["source_assets_root"] / kit["source_asset"]["filename"]
        data = path.read_bytes()
        # Preserve size to ensure the SHA check, not merely length, rejects it.
        path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        _expect_error(final, "SHA-256 does not match")
        path.unlink()
        _expect_error(final)  # Explicit authorization without either source fails.
        path.write_bytes(data)

    for field in ("legal_compliance_certified", "source_availability_certified", "release_payload_integrated"):
        changed = copy.deepcopy(policy)
        changed[field] = not changed[field]
        _expect_error(lambda: gate.validate_policy_document(changed))
    for historical in policy["releases"][:-1]:
        _assert(historical["status"] == "blocked" and tuple(historical["reason_codes"]) == gate.EXPECTED_REASONS[historical["tag"]], "historical release changed")
    changed = copy.deepcopy(owner)
    changed["legal_compliance_certified"] = True
    _expect_error(lambda: source_compliance.validate_owner(changed), "cannot authorize")

    auth_path = context["control_root"] / release_authorization.AUTHORIZATION_PATH
    saved = auth_path.read_bytes()
    auth = json.loads(saved)
    if auth["state"] == "LEGAL_RELEASE_AUTHORIZED":
        auth["reviewed_source_commit"] = "3" * 40
        auth_path.write_bytes(release_authorization.canonical_bytes(auth))
        _expect_error(final, "source commit mismatch")
        auth_path.write_bytes(saved)
        auth["reviewed_source_commit"] = SOURCE_COMMIT
        auth["reviewed_source_owner_sha256"] = "4" * 64
        auth_path.write_bytes(release_authorization.canonical_bytes(auth))
        _expect_error(final, "evidence mismatch")
        auth_path.write_bytes(saved)
    else:
        auth["state"] = "LEGAL_RELEASE_AUTHORIZED"
        auth_path.write_bytes(release_authorization.canonical_bytes(auth))
        _expect_error(final, "decision reference")
        auth_path.write_bytes(saved)

    cli = [sys.executable, str(REPO_ROOT / "scripts/verify_release_legal_gate.py"),
           "--policy", str(context["control_root"] / "legal/release-policy.json"), "--tag", TAG]
    prebuild = subprocess.run(cli + ["--stage", "prebuild"], capture_output=True, text=True, check=False)
    _assert(prebuild.returncode == 0 and "release_payload_integrated=false" in prebuild.stdout, "prebuild CLI integration claim invalid")
    missing = subprocess.run(cli, capture_output=True, text=True, check=False)
    _assert(missing.returncode == 2, "final CLI accepted absent evidence")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
