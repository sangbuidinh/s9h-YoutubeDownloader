from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import verify_source_correspondence as verifier


REPO_ROOT = Path(__file__).resolve().parents[1]
Mutation = Callable[[dict], None]


def main() -> int:
    correspondence, document = verifier.verify_repository(REPO_ROOT)
    _assert([kit["id"] for kit in document["kits"]] == ["aria2", "ffmpeg"], "kit IDs changed")
    _assert(all(kit["status"] == "blocked" for kit in document["kits"]), "source kit was unblocked")
    packages = {item["id"]: item for item in correspondence["packages"]}
    for kit in document["kits"]:
        package = packages[kit["id"]]
        _assert(kit["binary_package_sha256"] == package["binary_package"]["sha256"], "binary mapping changed")
        _assert("source-downloadable-from-same-release-location-as-binary" in kit["required_distribution_controls"], "same-location control missing")
        _assert("source-assets-independently-checksummed" in kit["required_distribution_controls"], "checksum control missing")
        _assert("retention-policy-requires-owner-legal-review" in kit["required_distribution_controls"], "retention review marker missing")
        static_ids = {
            item["id"] for item in package["external_components"] if item["linkage"] == "static"
        }
        _assert(
            {f"external-source:{item}" for item in static_ids}.issubset(kit["required_source_items"]),
            "external source requirements are incomplete",
        )

    mutations: tuple[tuple[str, Mutation], ...] = (
        ("source kit ready", lambda d: _kit(d, "aria2").update(status="ready")),
        ("release gate reconsideration", lambda d: d.update(release_gate_reconsideration_allowed=True)),
        ("missing external component source", _remove_external_source),
        ("missing build script", lambda d: _kit(d, "ffmpeg")["required_build_evidence"].remove("complete-build-orchestration-script")),
        ("missing configure information", lambda d: _kit(d, "ffmpeg")["required_source_items"].remove("exact-configure-command")),
        ("missing source checksum", lambda d: _kit(d, "aria2")["required_outputs"].remove("independent-source-asset-checksums")),
        ("missing binary-to-source mapping", lambda d: _kit(d, "ffmpeg")["required_distribution_controls"].remove("binary-to-source-mapping-published")),
        ("missing same-location access", lambda d: _kit(d, "aria2")["required_distribution_controls"].remove("source-downloadable-from-same-release-location-as-binary")),
        ("fixed retention period", _replace_retention_marker),
        ("bypass field", lambda d: d.update(bypass=True)),
        ("local path", lambda d: _kit(d, "ffmpeg")["blockers"].append("Use C:" + r"\Users\builder\source")),
        ("timestamp", lambda d: _kit(d, "aria2")["blockers"].append("Generated 2026-07-14T16:00")),
        ("compliance claim", lambda d: d.update(legal_compliance_certified=True)),
    )
    for label, mutation in mutations:
        _expect_document_failure(label, mutation)
    _expect_raw_failure("malformed JSON", b"{bad-json}\n")
    _expect_raw_transform_failure("BOM", lambda raw: b"\xef\xbb\xbf" + raw)
    _expect_raw_transform_failure("CRLF", lambda raw: raw.replace(b"\n", b"\r\n"))
    print("source kit requirements smoke tests passed")
    return 0


def _copy_fixture(root: Path) -> None:
    for relative in (
        "legal/source-correspondence.json",
        "legal/source-kit-requirements.json",
        "legal/release-policy.json",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "legal/README.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)


def _expect_document_failure(label: str, mutation: Mutation) -> None:
    def transform(root: Path) -> None:
        path = root / verifier.KIT_PATH
        document = json.loads(path.read_text(encoding="utf-8"))
        mutation(document)
        path.write_bytes(verifier.canonical_json_bytes(document))

    _expect_failure(label, transform)


def _expect_raw_failure(label: str, raw: bytes) -> None:
    _expect_failure(label, lambda root: (root / verifier.KIT_PATH).write_bytes(raw))


def _expect_raw_transform_failure(label: str, transform: Callable[[bytes], bytes]) -> None:
    def mutate(root: Path) -> None:
        path = root / verifier.KIT_PATH
        path.write_bytes(transform(path.read_bytes()))

    _expect_failure(label, mutate)


def _expect_failure(label: str, mutation: Callable[[Path], object]) -> None:
    with tempfile.TemporaryDirectory(prefix="source-kit-smoke-") as temp:
        root = Path(temp) / "repo"
        _copy_fixture(root)
        mutation(root)
        try:
            verifier.verify_repository(root)
        except (
            verifier.SourceCorrespondenceError,
            verifier.release_gate.ReleaseLegalGateError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            return
        raise AssertionError(f"mutation was not rejected: {label}")


def _kit(document: dict, kit_id: str) -> dict:
    return next(kit for kit in document["kits"] if kit["id"] == kit_id)


def _remove_external_source(document: dict) -> None:
    items = _kit(document, "aria2")["required_source_items"]
    items.remove(next(item for item in items if item.startswith("external-source:")))


def _replace_retention_marker(document: dict) -> None:
    controls = _kit(document, "ffmpeg")["required_distribution_controls"]
    controls.remove("retention-policy-requires-owner-legal-review")
    controls.append("source-assets-retained-for-three-years")
    controls.sort()


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
