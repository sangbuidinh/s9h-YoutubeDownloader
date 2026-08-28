from __future__ import annotations

import copy
from pathlib import Path

import ffmpeg_correspondence as evidence
import source_compliance
import release_authorization


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    value = evidence.load_record(ROOT / evidence.RECORD_PATH)
    assert evidence.validate_record(value) is False
    required = [row for row in value["direct_components"] if row["id"] in evidence.REQUIRED_IDS]
    assert len(required) == 48 and sum(row["resolved"] for row in required) == 24
    assert next(row for row in required if row["id"] == "openal")["resolved"] is False
    assert value["source_asset_created"] is False
    authorization = release_authorization.load_authorization(ROOT / release_authorization.AUTHORIZATION_PATH)
    assert authorization["state"] in {"LEGAL_REVIEW_REQUIRED", "LEGAL_RELEASE_AUTHORIZED"}
    import verify_release_legal_gate
    verify_release_legal_gate.validate_repository_control(ROOT)
    owner = source_compliance.load_owner(ROOT / source_compliance.OWNER_PATH)
    active = owner["kits"][1]
    if "runtime_build" in active:
        assert value["binary_package"] != active["binary_package"]
        assert active["runtime_build"]["historical_gyan_status"] == "retired-from-active-release-target"
    else:
        assert value["binary_package"] == active["binary_package"]
    assert owner["kits"][0]["source_asset"]["sha256"] == "bb609dca9589eea96676a3d608652ffc24ea381cbfc19476dd6e582a95f2fd15"
    mutations = (
        lambda v: v.__setitem__("verdict", "COMPLETE"),
        lambda v: v.__setitem__("legal_authorized", True),
        lambda v: v["direct_components"].pop(),
        lambda v: v["direct_components"][0].__setitem__("source_archive_sha256", None),
        lambda v: v["direct_components"][0].__setitem__("provider_version", "latest"),
        lambda v: v["direct_components"][0].__setitem__("immutable_ref", "main"),
        lambda v: v["system_dispositions"][-1].__setitem__("exclusion_certified", True),
        lambda v: v["transitive_components"].clear(),
    )
    for mutation in mutations:
        changed = copy.deepcopy(value)
        mutation(changed)
        try:
            evidence.validate_record(changed)
        except source_compliance.SourceComplianceError:
            pass
        else:
            raise AssertionError("invalid correspondence evidence accepted")
    try:
        evidence.require_ready_owner(value, owner)
    except source_compliance.SourceComplianceError as exc:
        assert "incomplete" in str(exc)
    else:
        raise AssertionError("incomplete provider correspondence was made ready")
    print("FFmpeg correspondence evidence smoke tests passed: 24/48 identities; incomplete and unauthorized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
