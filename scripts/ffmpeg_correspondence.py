"""Current, fail-closed FFmpeg correspondence evidence (not legal authorization)."""

from __future__ import annotations

import json
from pathlib import Path

import source_compliance as source


RECORD_PATH = "legal/ffmpeg-correspondence-v1.3.2.json"
DIRECT_IDS = set("amf avisynth bzlib cairo cuda cuda-llvm cuvid d3d11va d3d12va dxva2 ffnvcodec gmp gnutls iconv libaom libass libfontconfig libfreetype libfribidi libgme libgsm libharfbuzz libmfx libmp3lame libopencore-amrnb libopencore-amrwb libopenjpeg libopenmpt libopus librubberband libspeex libsrt libssh libtheora libvidstab libvmaf libvo-amrwbenc libvorbis libvpl libvpx libwebp libx264 libx265 libxml2 libxvid libzimg libzmq lzma mediafoundation nvdec nvenc openal sdl2 vaapi zlib".split())
SYSTEM_IDS = {"d3d11va", "d3d12va", "dxva2", "mediafoundation"}
REQUIRED_IDS = DIRECT_IDS - SYSTEM_IDS - {"cuda", "libmfx", "cuda-llvm"}


def load_record(path: Path) -> dict:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    source._require(raw == source.canonical_json_bytes(value), "FFmpeg evidence is not canonical UTF-8 LF JSON")
    validate_record(value)
    return value


def validate_record(value: dict) -> bool:
    require = source._require
    require(value.get("schema_version") == 1 and value.get("release_tag") == "v1.3.2", "FFmpeg evidence identity is invalid")
    direct = value["direct_components"]
    ids = [row["id"] for row in direct]
    require(ids == sorted(DIRECT_IDS), "FFmpeg direct inventory is incomplete or duplicated")
    required = []
    for row in direct:
        component = row["id"]
        expected = "REQUIRED_FOR_CORRESPONDING_SOURCE" if component in REQUIRED_IDS else (
            "SYSTEM_LIBRARY_CANDIDATE" if component in SYSTEM_IDS else (
                "REQUIRED_FOR_REPRODUCIBILITY_ASSURANCE" if component == "cuda-llvm" else "LEGACY_APPLICABILITY_REVIEW"))
        require(row["classification"] == expected, "FFmpeg source classification changed without inventory review")
        require(row["enabled"] is (component not in {"cuda", "libmfx"}), "FFmpeg configure applicability is invalid")
        require(row["configure_label"] == ("fontconfig" if component == "libfontconfig" else component), "FFmpeg configure alias is invalid")
        _validate_row(row)
        if component in REQUIRED_IDS:
            required.append(row)
    transitive = value["transitive_components"]
    transitive_ids = [row["id"] for row in transitive]
    require(bool(transitive) and len(transitive_ids) == len(set(transitive_ids)), "FFmpeg transitive inventory is empty or duplicated")
    require(not (set(transitive_ids) & DIRECT_IDS), "FFmpeg transitive identities duplicate direct inputs")
    for row in transitive:
        _validate_row(row)
        require(row["classification"] == "REQUIRED_FOR_CORRESPONDING_SOURCE", "FFmpeg transitive source was excluded")
    dispositions = value["system_dispositions"]
    require([row["id"] for row in dispositions] == sorted(SYSTEM_IDS) + ["vaapi"], "FFmpeg system dispositions are incomplete")
    for row in dispositions:
        require(type(row["complete"]) is bool and type(row["exclusion_certified"]) is bool, "FFmpeg disposition flags are invalid")
        source._validate_https_url(row["authority_url"], "system authority")
        require(bool(row["technical_evidence"]), "FFmpeg disposition evidence missing")
        if row["id"] == "vaapi":
            require(row["disposition"] == "SOURCE_REQUIRED_EXTERNAL" and row["exclusion_certified"] is False, "VAAPI cannot be excluded as a Windows System Library")
    for field in ("transitive_inventory_complete", "exact_provider_build_snapshot_proven", "exact_provider_patch_set_proven"):
        require(type(value[field]) is bool, "FFmpeg correspondence flag is invalid")
    complete = (all(row["resolved"] for row in required + transitive)
                and value["transitive_inventory_complete"]
                and all(row["complete"] for row in dispositions)
                and value["exact_provider_build_snapshot_proven"]
                and value["exact_provider_patch_set_proven"])
    require(value["verdict"] == ("COMPLETE" if complete else "INCOMPLETE"), "FFmpeg verdict does not follow evidence")
    require(value["legal_authorized"] is False, "technical FFmpeg evidence cannot authorize legal release")
    return complete


def _validate_row(row: dict) -> None:
    source._require(type(row["resolved"]) is bool, "FFmpeg input resolution flag is invalid")
    source._require(bool(row["provider_correspondence_evidence"]), "FFmpeg input evidence is missing")
    if not row["resolved"]:
        return
    source._require(bool(row["provider_version"]) and row["provider_version"] != "latest", "mutable provider identity cannot be resolved")
    source._require(row["immutable_identity_type"] in {"git-commit", "release-archive"}, "FFmpeg immutable identity type is invalid")
    if row["immutable_identity_type"] == "git-commit":
        source._require(bool(source.GIT_RE.fullmatch(row["immutable_ref"] or "")), "FFmpeg commit is not immutable")
    else:
        source._require(row["immutable_ref"] is None, "release archive has fabricated VCS identity")
    source._validate_file_identity({"filename": row["source_archive_filename"], "size": row["source_archive_size"], "sha256": row["source_archive_sha256"]}, "FFmpeg source input")
    source._require(row["source_archive_sha256"] != "0" * 64, "FFmpeg source hash is unsealed")
    source._validate_https_url(row["source_archive_url"], "FFmpeg source input")
    source._require(bool(row["license"]["archive_member"]) and bool(source.SHA256_RE.fullmatch(row["license"]["sha256"] or "")), "FFmpeg license evidence is unsealed")


def require_ready_owner(value: dict, owner: dict) -> None:
    source._require(validate_record(value), "FFmpeg correspondence remains incomplete")
    kit = owner["kits"][1]
    source._require(value["binary_package"] == kit["binary_package"], "FFmpeg binary/evidence mismatch")
    identities = {item["component_id"]: item for item in kit["identities"]}
    rows = [row for row in value["direct_components"] if row["id"] in REQUIRED_IDS] + value["transitive_components"]
    source._require(set(identities) == {"ffmpeg", "media-autobuild-suite"} | {row["id"] for row in rows}, "FFmpeg owner lacks complete direct/transitive identities")
    source._require(identities["ffmpeg"]["immutable_ref"] == value["core_commit"], "FFmpeg core evidence mismatch")
    for row in rows:
        item = identities[row["id"]]
        for field, evidence_field in (("identity_type", "immutable_identity_type"), ("immutable_ref", "immutable_ref"), ("archive_filename", "source_archive_filename"), ("archive_url", "source_archive_url"), ("archive_size", "source_archive_size"), ("archive_sha256", "source_archive_sha256")):
            source._require(item[field] == row[evidence_field], f"FFmpeg owner/input mismatch: {row['id']} {field}")
