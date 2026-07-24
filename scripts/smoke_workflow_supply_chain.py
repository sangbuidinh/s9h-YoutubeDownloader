import copy
import datetime
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
PIN_INVENTORY_PATH = REPO_ROOT / ".github" / "actions-pins.json"

EXPECTED_WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/prerelease-v1.2.7-rc.1.yml",
    ".github/workflows/prerelease-v1.3.0-rc.1.yml",
    ".github/workflows/release-v1.3.0.yml",
    ".github/workflows/release-v1.3.1.yml",
)
CI_WORKFLOW = ".github/workflows/ci.yml"
FORBIDDEN_VERSION_WORKFLOW_TRIGGERS = {
    "branch_protection_rule",
    "pull_request",
    "pull_request_target",
    "push",
    "release",
    "repository_dispatch",
    "schedule",
    "workflow_run",
}
CURRENT_PROFILE = "current_ci"
HISTORICAL_PROFILE = "frozen_historical_release"
CURRENT_CI_ACTIONS = {
    "actions/checkout": {
        "repository": "actions/checkout",
        "release_tag": "v6.1.0",
        "workflow_comment": "v6.1.0",
        "commit": "d23441a48e516b6c34aea4fa41551a30e30af803",
        "declared_runtime": "node24",
        "official_repository": True,
        "action_yml_blob": "5b0524f730db83f9513c18ab31a6c086c7239076",
        "lifecycle": "current",
        "occurrence_count": 1,
    },
    "actions/setup-python": {
        "repository": "actions/setup-python",
        "release_tag": "v6.3.0",
        "workflow_comment": "v6.3.0",
        "commit": "ece7cb06caefa5fff74198d8649806c4678c61a1",
        "declared_runtime": "node24",
        "official_repository": True,
        "action_yml_blob": "7a9a7b634ec348b35b882f1f14fcaa4d41836a8e",
        "lifecycle": "current",
        "occurrence_count": 1,
    },
    "actions/upload-artifact": {
        "repository": "actions/upload-artifact",
        "release_tag": "v7.0.1",
        "workflow_comment": "v7.0.1",
        "commit": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "declared_runtime": "node24",
        "official_repository": True,
        "action_yml_blob": "7cb4d1e81db55320b41217e1a78a1a46e3d2baef",
        "lifecycle": "current",
        "occurrence_count": 1,
    },
    "actions/download-artifact": {
        "repository": "actions/download-artifact",
        "release_tag": "v8.0.1",
        "workflow_comment": "v8.0.1",
        "commit": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
        "declared_runtime": "node24",
        "official_repository": True,
        "action_yml_blob": "8b8c65029ccad20750a29fecb438eca5a607fc57",
        "lifecycle": "current",
        "occurrence_count": 1,
    },
}
HISTORICAL_ACTIONS = {
    "actions/checkout": {
        "repository": "actions/checkout",
        "release_tag": "v4.3.1",
        "workflow_comment": "v4",
        "commit": "34e114876b0b11c390a56381ad16ebd13914f8d5",
        "declared_runtime": "node20",
        "official_repository": True,
        "action_yml_blob": "6842eb843b7258993656f41f9c358f5c5331fbe7",
        "lifecycle": "frozen-historical",
        "occurrence_count": 11,
    },
    "actions/setup-python": {
        "repository": "actions/setup-python",
        "release_tag": "v5.6.0",
        "workflow_comment": "v5",
        "commit": "a26af69be951a213d495a4c3e4e4022e16d87065",
        "declared_runtime": "node20",
        "official_repository": True,
        "action_yml_blob": "efa8de904209196588db1453bdb44079b3c393d7",
        "lifecycle": "frozen-historical",
        "occurrence_count": 8,
    },
    "actions/upload-artifact": {
        "repository": "actions/upload-artifact",
        "release_tag": "v4.6.2",
        "workflow_comment": "v4",
        "commit": "ea165f8d65b6e75b540449e92b4886f43607fa02",
        "declared_runtime": "node20",
        "official_repository": True,
        "action_yml_blob": "2a0ecf19e8d0087dd2e5d1785dcf764811e79fae",
        "lifecycle": "frozen-historical",
        "occurrence_count": 4,
    },
    "actions/download-artifact": {
        "repository": "actions/download-artifact",
        "release_tag": "v4.3.0",
        "workflow_comment": "v4",
        "commit": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "declared_runtime": "node20",
        "official_repository": True,
        "action_yml_blob": "7fc4fb55c7d0c7b198b1c7466e1efd7c7d05fb26",
        "lifecycle": "frozen-historical",
        "occurrence_count": 4,
    },
    "softprops/action-gh-release": {
        "repository": "softprops/action-gh-release",
        "release_tag": "v2.6.2",
        "workflow_comment": "v2",
        "commit": "3bb12739c298aeb8a4eeaf626c5b8d85266b0e65",
        "declared_runtime": "node20",
        "official_repository": False,
        "action_yml_blob": "b471d236bc28052c1d78c3d6b57ee480d192da6a",
        "lifecycle": "frozen-historical",
        "occurrence_count": 4,
    },
}
EXPECTED_WORKFLOW_PROFILES = {
    CI_WORKFLOW: CURRENT_PROFILE,
    ".github/workflows/prerelease-v1.2.7-rc.1.yml": HISTORICAL_PROFILE,
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": HISTORICAL_PROFILE,
    ".github/workflows/release-v1.3.0.yml": HISTORICAL_PROFILE,
    ".github/workflows/release-v1.3.1.yml": HISTORICAL_PROFILE,
}
EXPECTED_WORKFLOW_ACTION_COUNTS = {
    CI_WORKFLOW: {
        "actions/checkout": 1,
        "actions/setup-python": 1,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 1,
    },
    ".github/workflows/prerelease-v1.2.7-rc.1.yml": {
        "actions/checkout": 2,
        "actions/setup-python": 2,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 1,
        "softprops/action-gh-release": 1,
    },
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": {
        "actions/checkout": 3,
        "actions/setup-python": 2,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 1,
        "softprops/action-gh-release": 1,
    },
    ".github/workflows/release-v1.3.0.yml": {
        "actions/checkout": 3,
        "actions/setup-python": 2,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 1,
        "softprops/action-gh-release": 1,
    },
    ".github/workflows/release-v1.3.1.yml": {
        "actions/checkout": 3,
        "actions/setup-python": 2,
        "actions/upload-artifact": 1,
        "actions/download-artifact": 1,
        "softprops/action-gh-release": 1,
    },
}
HISTORICAL_WORKFLOW_BLOBS = {
    ".github/workflows/prerelease-v1.2.7-rc.1.yml": "613c74ba98d9aa801b839d6a84b123b38aafbe2a",
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": "84378adce76b25e6c50bbac138de8183c83eddcf",
    ".github/workflows/release-v1.3.0.yml": "753f12a9745493b46160229c47498aeb273f26eb",
    ".github/workflows/release-v1.3.1.yml": "620114ff51dcd5a41ba6dbeec0c922630682bdf9",
}
LEGACY_WORKFLOW = ".github/workflows/prerelease-v1.2.7-rc.1.yml"
FIXED_TAG_WORKFLOWS = {
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": "v1.3.0-rc.1",
    ".github/workflows/release-v1.3.0.yml": "v1.3.0",
    ".github/workflows/release-v1.3.1.yml": "v1.3.1",
}
RELEASE_POLICY = {
    ".github/workflows/prerelease-v1.2.7-rc.1.yml": (
        "v1.2.7-rc.1",
        r".\scripts\build_prerelease_v1_2_7_rc1.ps1",
    ),
    ".github/workflows/prerelease-v1.3.0-rc.1.yml": (
        "v1.3.0-rc.1",
        r".\scripts\build_prerelease_v1_3_0_rc1.ps1 -PreparePinnedRuntime",
    ),
    ".github/workflows/release-v1.3.0.yml": (
        "v1.3.0",
        r".\scripts\build_release_v1_3_0.ps1 -PreparePinnedRuntime",
    ),
    ".github/workflows/release-v1.3.1.yml": (
        "v1.3.1",
        r".\scripts\build_release_v1_3_1.ps1 -PreparePinnedRuntime",
    ),
}
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
ACTION_LINE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^@\s#]+)@([^\s#]+)\s+#\s*"
    r"(v\d+(?:\.\d+\.\d+)?)\s*$"
)
PERMISSION_LINE = re.compile(
    r"^(\s*)(contents|actions|checks|packages|pull-requests|"
    r"id-token|issues|deployments)\s*:\s*(read|write)\s*$"
)


class SupplyChainContractError(AssertionError):
    pass


def main() -> int:
    documents = _load_workflows()
    inventory = _load_inventory()
    validate_supply_chain(documents, inventory)
    owner_negative_count = _test_current_owner_delegation()
    r3a_negative_count = _test_r3a_negative_mutations(documents, inventory)
    _test_negative_mutations(documents, inventory)
    print(
        "workflow supply-chain smoke tests passed: "
        f"18 R3a positive contracts, {r3a_negative_count} R3a negative mutations, "
        f"{owner_negative_count} current-owner negative regressions"
    )
    return 0


def validate_supply_chain(documents: dict[str, str], inventory: dict) -> None:
    _require(
        tuple(sorted(documents)) == EXPECTED_WORKFLOWS,
        "workflow file inventory differs from the expected five files",
    )
    profiles = _validate_inventory(inventory)
    workflow_profiles = inventory["workflow_profiles"]
    expected_workflow_counts = inventory["workflow_action_counts"]

    uses_by_profile_action: dict[
        tuple[str, str], list[tuple[str, str, str]]
    ] = defaultdict(list)
    for path, workflow in documents.items():
        if path in HISTORICAL_WORKFLOW_BLOBS:
            _require(
                _git_blob_sha1(workflow.encode("utf-8"))
                == HISTORICAL_WORKFLOW_BLOBS[path],
                f"{path} differs from its frozen historical Git blob",
            )
        workflow = _normalize_newlines(workflow)
        _validate_trigger_policy(path, workflow)
        _validate_workflow_environment(path, workflow)
        _validate_permissions(path, workflow)
        _validate_historical_behavior(path, workflow)
        _validate_action_placement(path, workflow)
        if path == CI_WORKFLOW:
            _validate_current_ci_action_controls(workflow)
        _verify_no_sensitive_literals(path, workflow)

        uses_lines = [
            (number, line)
            for number, line in enumerate(workflow.splitlines(), 1)
            if re.match(r"^\s*(?:-\s*)?uses\s*:", line)
        ]
        _require(bool(uses_lines), f"{path} contains no action invocation")
        profile_name = workflow_profiles[path]
        profile_actions = profiles[profile_name]["actions"]
        workflow_counts = Counter()
        for number, line in uses_lines:
            stripped = line.strip()
            _require(
                not stripped.startswith(("uses: ./", "- uses: ./")),
                f"{path}:{number} local action is forbidden",
            )
            _require(
                "docker://" not in stripped.casefold(),
                f"{path}:{number} Docker action is forbidden",
            )
            _require(
                "/.github/workflows/" not in stripped,
                f"{path}:{number} reusable workflow call is forbidden",
            )
            match = ACTION_LINE.fullmatch(line)
            _require(
                match is not None,
                f"{path}:{number} action must use full SHA and version comment",
            )
            repository, commit, comment = match.groups()
            _require(
                repository in profile_actions,
                f"{path}:{number} action is not allowlisted by profile {profile_name}",
            )
            expected = profile_actions[repository]
            _require(
                FULL_SHA.fullmatch(commit) is not None,
                f"{path}:{number} action ref must be lowercase 40-character SHA",
            )
            _require(
                comment == expected["workflow_comment"],
                f"{path}:{number} action version comment is incorrect",
            )
            _require(
                commit == expected["commit"],
                f"{path}:{number} action ref differs from pin inventory",
            )
            workflow_counts[repository] += 1
            uses_by_profile_action[(profile_name, repository)].append(
                (path, commit, comment)
            )
        _require(
            dict(workflow_counts) == expected_workflow_counts[path],
            f"{path} action occurrence counts differ: {workflow_counts}",
        )

    total_count = sum(len(rows) for rows in uses_by_profile_action.values())
    _require(total_count == 35, "total immutable action count must be 35")
    for profile_name, profile in profiles.items():
        for repository, entry in profile["actions"].items():
            rows = uses_by_profile_action[(profile_name, repository)]
            _require(
                len(rows) == entry["occurrence_count"],
                f"{profile_name}/{repository} occurrence count differs",
            )
    for (profile_name, repository), rows in uses_by_profile_action.items():
        _require(
            len({commit for _, commit, _ in rows}) == 1,
            f"{profile_name}/{repository} uses inconsistent SHAs across workflows",
        )


def _load_workflows() -> dict[str, str]:
    paths = sorted(WORKFLOW_DIR.glob("*.yml"))
    return {
        path.relative_to(REPO_ROOT).as_posix(): path.read_bytes().decode("utf-8")
        for path in paths
    }


def _load_inventory() -> dict:
    _require(PIN_INVENTORY_PATH.is_file(), "action pin inventory is missing")
    try:
        return json.loads(PIN_INVENTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SupplyChainContractError(f"action pin inventory is invalid JSON: {exc}") from exc


def _validate_inventory(inventory: dict) -> dict:
    _require(
        set(inventory)
        == {
            "schema_version",
            "resolved_at_utc",
            "profiles",
            "workflow_profiles",
            "workflow_action_counts",
        },
        "pin inventory top-level fields differ",
    )
    _require(inventory.get("schema_version") == 2, "pin inventory schema must be 2")
    resolved = inventory.get("resolved_at_utc")
    _require(isinstance(resolved, str) and resolved.endswith("Z"), "resolution time is invalid")
    try:
        datetime.datetime.fromisoformat(resolved.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise SupplyChainContractError("resolution time is not ISO-8601 UTC") from exc

    profiles = inventory.get("profiles")
    _require(
        isinstance(profiles, dict)
        and set(profiles) == {CURRENT_PROFILE, HISTORICAL_PROFILE},
        "pin inventory profiles differ",
    )
    expected_profiles = {
        CURRENT_PROFILE: {
            "lifecycle": "current",
            "recommended_for_new_workflows": True,
            "actions": CURRENT_CI_ACTIONS,
        },
        HISTORICAL_PROFILE: {
            "lifecycle": "frozen-historical",
            "recommended_for_new_workflows": False,
            "actions": HISTORICAL_ACTIONS,
        },
    }
    for profile_name, expected_profile in expected_profiles.items():
        profile = profiles[profile_name]
        _require(
            isinstance(profile, dict)
            and set(profile)
            == {"lifecycle", "recommended_for_new_workflows", "actions"},
            f"{profile_name} profile fields differ",
        )
        _require(
            profile["lifecycle"] == expected_profile["lifecycle"],
            f"{profile_name} lifecycle is incorrect",
        )
        _require(
            profile["recommended_for_new_workflows"]
            is expected_profile["recommended_for_new_workflows"],
            f"{profile_name} recommendation state is incorrect",
        )
        actions = profile["actions"]
        expected_actions = expected_profile["actions"]
        _require(
            isinstance(actions, dict) and set(actions) == set(expected_actions),
            f"{profile_name} action set differs",
        )
        for repository, expected_entry in expected_actions.items():
            entry = actions[repository]
            _require(
                isinstance(entry, dict) and set(entry) == set(expected_entry),
                f"{profile_name}/{repository} pin entry fields differ",
            )
            _require(
                entry == expected_entry,
                f"{profile_name}/{repository} pin identity differs",
            )
            _require(
                FULL_SHA.fullmatch(entry["commit"]) is not None,
                f"{profile_name}/{repository} commit must be a full lowercase SHA",
            )
            _require(
                FULL_SHA.fullmatch(entry["action_yml_blob"]) is not None,
                f"{profile_name}/{repository} action.yml blob must be a full SHA",
            )
            _require(
                re.fullmatch(r"v\d+\.\d+\.\d+", entry["release_tag"]) is not None,
                f"{profile_name}/{repository} release tag must be stable semantic version",
            )
    _require(
        inventory.get("workflow_profiles") == EXPECTED_WORKFLOW_PROFILES,
        "workflow profile mapping differs",
    )
    _require(
        inventory.get("workflow_action_counts") == EXPECTED_WORKFLOW_ACTION_COUNTS,
        "per-workflow action occurrence counts differ",
    )
    _require(
        "softprops/action-gh-release" not in profiles[CURRENT_PROFILE]["actions"],
        "current CI profile must not contain a publishing action",
    )
    return profiles


def _validate_workflow_environment(path: str, workflow: str) -> None:
    runners = re.findall(r"(?m)^\s*runs-on:\s*(\S+)\s*$", workflow)
    _require(
        runners == ["windows-2022", "windows-2022"],
        f"{path} jobs must use windows-2022",
    )

    versions = re.findall(r'(?m)^\s*python-version:\s*"?([^"\s]+)"?\s*$', workflow)
    expected_versions = ["3.11.9"] if path == CI_WORKFLOW else ["3.11.9", "3.11.9"]
    _require(versions == expected_versions, f"{path} Python selectors must be exact 3.11.9")
    for required in (
        "python --version",
        '$VersionOutput = (& python --version 2>&1 | Out-String).Trim()',
        'if ($VersionOutput -ne "Python 3.11.9")',
        'Write-Host "Pinned Python verified: $VersionOutput"',
    ):
        _require(required in workflow, f"{path} exact Python verification is missing")
    if path != CI_WORKFLOW:
        _require(
            'Write-Host "Pinned publish Python verified: $VersionOutput"' in workflow,
            f"{path} exact publish Python verification is missing",
        )


def _validate_trigger_policy(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    trigger_block = _mapping_block(lines, "on", 0)
    event_keys = _direct_mapping_keys(trigger_block, 2)

    if path == CI_WORKFLOW:
        _require(
            event_keys == ["pull_request", "push"],
            "CI direct triggers must be pull_request and push only",
        )
        for event in event_keys:
            event_block = _mapping_block(trigger_block, event, 2)
            _require(
                _direct_mapping_keys(event_block, 4) == ["branches"],
                f"CI {event} must define only a branches filter",
            )
            branches_block = _mapping_block(event_block, "branches", 4)
            _require(
                _direct_sequence_values(branches_block, 6) == ["main"],
                f"CI {event} must target main only",
            )
        return

    forbidden = FORBIDDEN_VERSION_WORKFLOW_TRIGGERS.intersection(event_keys)
    _require(
        not forbidden,
        f"{path} contains automatic trigger(s): {sorted(forbidden)}",
    )
    _require(
        event_keys == ["workflow_dispatch"],
        f"{path} direct trigger must be workflow_dispatch only",
    )


def _validate_permissions(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    top = _mapping_block(lines, "permissions", 0)
    _require(
        _direct_mapping_pairs(top, 2) == [("contents", "read")],
        f"{path} top-level permissions must be contents read only",
    )
    writes = []
    for number, line in enumerate(lines, 1):
        match = PERMISSION_LINE.fullmatch(line)
        if match and match.group(3) == "write":
            writes.append((number, len(match.group(1)), match.group(2)))

    if path == ".github/workflows/ci.yml":
        _require(not writes, "CI must not contain job-level write permission")
        for job_name in ("windows-smoke", "release-bundle-handoff"):
            job = _mapping_block(lines, job_name, 2)
            _require(
                _direct_mapping_pairs(_mapping_block(job, "permissions", 4), 6)
                == [("contents", "read")],
                f"CI {job_name} job must have contents read only",
            )
        return

    build_job = _mapping_block(lines, "build", 2)
    _require(
        _direct_mapping_pairs(_mapping_block(build_job, "permissions", 4), 6)
        == [("contents", "read")],
        f"{path} build job must have contents read only",
    )
    publish_job = _mapping_block(lines, "publish", 2)
    job_permissions = _mapping_block(publish_job, "permissions", 4)
    _require(
        _direct_mapping_pairs(job_permissions, 6) == [("contents", "write")],
        f"{path} publish job must have contents write only",
    )
    _require(
        len(writes) == 1 and writes[0][1:] == (6, "contents"),
        f"{path} contains unexpected write permissions",
    )


def _validate_historical_behavior(path: str, workflow: str) -> None:
    if path == ".github/workflows/ci.yml":
        _require(
            "softprops/action-gh-release@" not in workflow,
            "CI must not contain a publishing action",
        )
        return
    tag, build_command = RELEASE_POLICY[path]
    _require(tag in workflow, f"{path} historical tag is missing")
    _require(build_command in workflow, f"{path} build command changed")
    _require(
        "softprops/action-gh-release@" in workflow,
        f"{path} publishing action is missing",
    )
    _validate_dependency_install(path, workflow, tag, build_command)
    _validate_release_legal_integration(path, workflow, tag, build_command)


def _validate_action_placement(path: str, workflow: str) -> None:
    lines = workflow.splitlines()
    if path == CI_WORKFLOW:
        producer = "\n".join(_mapping_block(lines, "windows-smoke", 2))
        consumer = "\n".join(_mapping_block(lines, "release-bundle-handoff", 2))
        _require(producer.count("actions/upload-artifact@") == 1, "CI producer upload action count")
        _require("actions/download-artifact@" not in producer, "CI producer must not download")
        _require(consumer.count("actions/download-artifact@") == 1, "CI consumer download action count")
        for forbidden in (
            "actions/checkout@",
            "actions/setup-python@",
            "actions/upload-artifact@",
            "softprops/action-gh-release@",
        ):
            _require(forbidden not in consumer, f"CI consumer contains forbidden action: {forbidden}")
        _validate_download_inputs(consumer, "${{ needs.windows-smoke.outputs.artifact-id }}", "CI consumer")
        return

    build_block = _mapping_block(lines, "build", 2)
    publish_block = _mapping_block(lines, "publish", 2)
    _require(
        _scalar_value(publish_block, "needs", 4) == "build",
        f"{path} publish job must need build",
    )
    build = "\n".join(build_block)
    publish = "\n".join(publish_block)
    _require(build.count("actions/upload-artifact@") == 1, f"{path} build upload action count")
    _require("actions/download-artifact@" not in build, f"{path} build must not download")
    _require("softprops/action-gh-release@" not in build, f"{path} build must not publish")
    _require(publish.count("actions/download-artifact@") == 1, f"{path} publish download action count")
    _require(publish.count("softprops/action-gh-release@") == 1, f"{path} publish action count")
    _require(publish.count("actions/checkout@") == 1, f"{path} publish checkout action count")
    _require(publish.count("actions/setup-python@") == 1, f"{path} publish setup-python action count")
    for forbidden in ("actions/upload-artifact@",):
        _require(forbidden not in publish, f"{path} publish contains forbidden action: {forbidden}")
    publish_steps = _step_blocks(publish_block)
    publish_checkout = _action_steps(publish_steps, "actions/checkout")[0]
    _require(
        _direct_mapping_pairs(_mapping_block(publish_checkout, "with", 8), 10)
        == [
            ("ref", "${{ needs.build.outputs.control-commit }}"),
            ("path", "control"),
            ("persist-credentials", "false"),
        ],
        f"{path} publish control checkout inputs changed",
    )
    publish_setup = _action_steps(publish_steps, "actions/setup-python")[0]
    _require(
        _direct_mapping_pairs(_mapping_block(publish_setup, "with", 8), 10)
        == [("python-version", "3.11.9")],
        f"{path} publish Python selector changed",
    )
    _validate_download_inputs(publish, "${{ needs.build.outputs.artifact-id }}", path)


def _validate_release_legal_integration(
    path: str,
    workflow: str,
    tag: str,
    build_command: str,
) -> None:
    jobs = _mapping_block(workflow.splitlines(), "jobs", 0)
    build = _mapping_block(jobs, "build", 2)
    publish = _mapping_block(jobs, "publish", 2)
    build_steps = _step_blocks(build)
    publish_steps = _step_blocks(publish)
    build_step = next(
        (step for step in build_steps if build_command in "\n".join(step)),
        None,
    )
    _require(build_step is not None, f"{path} build step is unavailable")
    legal_step = _named_step(build_steps, "Prepare and verify release legal payload")
    bundle_step = _named_step(build_steps, "Create and verify release bundle")
    upload_step = _action_steps(build_steps, "actions/upload-artifact")[0]
    _require(
        build_steps.index(build_step)
        < build_steps.index(legal_step)
        < build_steps.index(bundle_step)
        < build_steps.index(upload_step),
        f"{path} build/legal/bundle order is invalid",
    )
    legal_text = "\n".join(legal_step)
    bundle_text = "\n".join(bundle_step)
    for required in (
        "prepare_release_legal_payload.py create",
        "prepare_release_legal_payload.py verify",
        "--release-notes release/RELEASE_NOTES.md",
        f"Youtube-Downloaderbs-{tag}-legal.zip",
    ):
        _require(required in legal_text, f"{path} legal payload integration is missing: {required}")
    _require(
        legal_text.count("--release-notes release/RELEASE_NOTES.md") == 2,
        f"{path} legal payload release-notes argument count changed",
    )
    for required in (
        "prepare_release_bundle.py create",
        "prepare_release_bundle.py verify",
        "release-assets-v2.json",
        "--source-assets-root release/source-assets",
        "--require-release-ready false",
    ):
        _require(required in bundle_text, f"{path} bundle v2 integration is missing: {required}")
    _require(
        re.search(
            r"(?i)(?:New-Item|Set-Content|write_bytes|writestr)[^\n]*source-assets",
            "\n".join(build),
        )
        is None,
        f"{path} creates an empty source placeholder",
    )

    verifier = _named_step(publish_steps, "Verify downloaded release bundle")
    verifier_text = "\n".join(verifier)
    for required in (
        "s9h-release-bundle-v2",
        "legal-payload",
        "aria2-source",
        "ffmpeg-source",
    ):
        _require(required in verifier_text, f"{path} publish verifier is missing: {required}")

    publish_checkout = _action_steps(publish_steps, "actions/checkout")[0]
    publish_setup = _action_steps(publish_steps, "actions/setup-python")[0]
    publish_python_check = _named_step(publish_steps, "Verify publish Python version")
    download_step = _action_steps(publish_steps, "actions/download-artifact")[0]
    publish_ready = _named_step(publish_steps, "Enforce publish-ready release bundle")
    publish_ready_text = "\n".join(publish_ready)
    _require(
        len(
            re.findall(
                r"(?m)^\s*python control/scripts/prepare_release_bundle\.py verify\s*`?\s*$",
                publish_ready_text,
            )
        )
        == 1,
        f"{path} must execute one real publish-ready verifier",
    )
    for required in (
        "--bundle-root release-bundle",
        f"--tag {tag}",
        '--source-commit "${{ needs.build.outputs.source-commit }}"',
        '--control-commit "${{ needs.build.outputs.control-commit }}"',
        "--policy control/legal/release-policy.json",
        "--asset-contract control/legal/release-assets-v2.json",
        f"--legal-payload release-bundle/assets/Youtube-Downloaderbs-{tag}-legal.zip",
        "--source-assets-root release-bundle/assets",
        "--require-release-ready true",
        "release bundle is not approved for publishing",
    ):
        _require(required in publish_ready_text, f"{path} publish-ready command is missing: {required}")
    _require(
        re.search(r"(?m)^\s+(?:continue-on-error|if)\s*:", publish_ready_text) is None,
        f"{path} publish-ready command contains a YAML bypass",
    )

    output_validation = _named_step(publish_steps, "Validate immutable build outputs")
    absence = _named_step(publish_steps, "Confirm release absence immediately before publishing")
    release_step = _action_steps(publish_steps, "softprops/action-gh-release")[0]
    _require(
        publish_steps.index(output_validation)
        < publish_steps.index(publish_checkout)
        < publish_steps.index(publish_setup)
        < publish_steps.index(publish_python_check)
        < publish_steps.index(download_step)
        < publish_steps.index(verifier)
        < publish_steps.index(publish_ready)
        < publish_steps.index(absence)
        < publish_steps.index(release_step),
        f"{path} publish verification order changed",
    )
    release_text = "\n".join(release_step)
    for filename in (
        f"Youtube-Downloaderbs-{tag}-legal.zip",
        f"Youtube-Downloaderbs-{tag}-aria2-source.zip",
        f"Youtube-Downloaderbs-{tag}-ffmpeg-source.zip",
    ):
        _require(filename in release_text, f"{path} publish asset is missing: {filename}")
    _require("fail_on_unmatched_files: true" in release_text, f"{path} unmatched files gate changed")


def _validate_download_inputs(job: str, artifact_id: str, label: str) -> None:
    download_steps = _action_steps(_step_blocks(job.splitlines()), "actions/download-artifact")
    _require(len(download_steps) == 1, f"{label} download action count is invalid")
    download_with = _mapping_block(download_steps[0], "with", 8)
    _require(
        _scalar_value(download_with, "merge-multiple", 10) == "true",
        f"{label} merge-multiple must be the YAML boolean true",
    )
    expected_inputs = [
        ("artifact-ids", artifact_id),
        ("path", "release-bundle"),
        ("merge-multiple", "true"),
    ]
    if label == "CI consumer":
        expected_inputs.append(("digest-mismatch", "error"))
    _require(
        _direct_mapping_pairs(download_with, 10) == expected_inputs,
        f"{label} download inputs must select one artifact ID and release-bundle",
    )


def _validate_current_ci_action_controls(workflow: str) -> None:
    jobs = _mapping_block(workflow.splitlines(), "jobs", 0)
    producer = _mapping_block(jobs, "windows-smoke", 2)
    steps = _step_blocks(producer)
    checkout = _action_steps(steps, "actions/checkout")
    _require(len(checkout) == 1, "CI checkout action count is invalid")
    checkout_with = _mapping_block(checkout[0], "with", 8)
    _require(
        _direct_mapping_pairs(checkout_with, 10)
        == [("fetch-depth", "0"), ("persist-credentials", "false")],
        "CI checkout must use full history without persisted credentials",
    )
    upload = _action_steps(steps, "actions/upload-artifact")
    _require(len(upload) == 1, "CI upload action count is invalid")
    upload_with = _mapping_block(upload[0], "with", 8)
    _require(
        _direct_mapping_pairs(upload_with, 10)
        == [
            ("name", "ci-release-bundle-${{ github.run_id }}-${{ github.run_attempt }}"),
            (
                "path",
                "${{ runner.temp }}/s9h-ci-release-${{ github.run_id }}-${{ github.run_attempt }}/publish-bundle",
            ),
            ("if-no-files-found", "error"),
            ("compression-level", "0"),
            ("overwrite", "false"),
            ("include-hidden-files", "false"),
            ("retention-days", "1"),
        ],
        "CI upload artifact behavior changed",
    )
    _require(
        re.search(
            r"(?im)^\s*git\s+(?:push|fetch|pull|clone|submodule)\b",
            workflow,
        )
        is None,
        "CI must not require persisted Git credentials",
    )


def _validate_dependency_install(
    path: str,
    workflow: str,
    tag: str,
    build_command: str,
) -> None:
    _require(
        re.search(
            r"(?im)^\s*(?:run:\s*)?(?:python\s+-m\s+)?pip\s+install\b",
            workflow,
        )
        is None,
        f"{path} must not contain a direct pip install",
    )
    for forbidden in (
        "--extra-index-url",
        "--trusted-host",
        "--pre",
        "--no-binary",
        "--only-binary=:none:",
    ):
        _require(
            re.search(rf"(?m)(?:^|\s){re.escape(forbidden)}(?:\s|=|$)", workflow)
            is None,
            f"{path} contains unsafe installer input",
        )

    job = _mapping_block(workflow.splitlines(), "build", 2)
    steps = _step_blocks(job)
    installer = _named_step(steps, "Install locked build dependencies")
    installer_text = "\n".join(installer)
    installer_command = (
        "python scripts/install_build_dependencies.py"
        if path == LEGACY_WORKFLOW
        else "python control/scripts/install_build_dependencies.py"
    )
    for required in (
        installer_command,
        '$env:RUNNER_TEMP\\s9h-build-venv-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT',
        '--github-env "$env:GITHUB_ENV"',
        '--github-path "$env:GITHUB_PATH"',
    ):
        _require(required in installer_text, f"{path} installer is missing: {required}")
    _require(
        "\n".join(job).count(installer_command) == 1,
        f"{path} must invoke the locked installer exactly once",
    )

    setup_steps = _action_steps(steps, "actions/setup-python")
    checkout_steps = _action_steps(steps, "actions/checkout")
    _require(len(setup_steps) == 1, f"{path} must set up Python exactly once")
    python_verification = _named_step(steps, "Verify pinned Python version")
    legal_gate = _named_step(steps, "Enforce fail-closed release legal gate")
    _validate_release_legal_gate(path, tag, steps, legal_gate)
    build_steps = [step for step in steps if build_command in "\n".join(step)]
    _require(len(build_steps) == 1, f"{path} build step is ambiguous")
    _require(
        steps.index(setup_steps[0])
        < steps.index(python_verification)
        < steps.index(legal_gate)
        < steps.index(installer)
        < steps.index(build_steps[0]),
        f"{path} legal gate and installer order is invalid",
    )

    if path == LEGACY_WORKFLOW:
        _require(len(checkout_steps) == 1, "legacy workflow must use one source checkout")
        _require(
            _step_name(checkout_steps[0]) == "Check out source",
            "legacy workflow must retain current-source build semantics",
        )
        _require(
            _scalar_value(checkout_steps[0], "ref", 10) is None,
            "legacy source checkout must not select a historical tag",
        )
        return

    _require(path in FIXED_TAG_WORKFLOWS, f"unexpected version workflow: {path}")
    _require(len(checkout_steps) == 2, f"{path} must use two checkout steps")
    lock_checkout, tag_checkout = checkout_steps
    _require(
        _step_name(lock_checkout) == "Check out workflow control source",
        f"{path} control-source checkout is missing",
    )
    _require(
        _scalar_value(lock_checkout, "ref", 10) is None,
        f"{path} control-source checkout must use dispatch source",
    )
    _require(
        _unquote(_scalar_value(lock_checkout, "path", 10) or "") == "control",
        f"{path} control-source checkout path changed",
    )
    _require(
        _step_name(tag_checkout) == "Check out release tag",
        f"{path} release-tag checkout is missing",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "ref", 10) or "") == tag,
        f"{path} release-tag checkout ref changed",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "fetch-depth", 10) or "") == "0",
        f"{path} release-tag checkout must use fetch-depth 0",
    )
    _require(
        _unquote(_scalar_value(tag_checkout, "path", 10) or "") == "source",
        f"{path} release-tag checkout path changed",
    )
    verification = _named_step(steps, "Verify annotated tag and release absence")
    canonical_temp = _named_step(steps, "Configure canonical Windows temp path")
    _require(
        steps.index(lock_checkout)
        < steps.index(setup_steps[0])
        < steps.index(python_verification)
        < steps.index(legal_gate)
        < steps.index(installer)
        < steps.index(tag_checkout)
        < steps.index(verification)
        < steps.index(canonical_temp)
        < steps.index(build_steps[0]),
        f"{path} fixed-tag dependency installation order is invalid",
    )


def _validate_release_legal_gate(
    path: str,
    tag: str,
    steps: list[list[str]],
    gate_step: list[str],
) -> None:
    prefix = "" if path == LEGACY_WORKFLOW else "control/"
    gate_text = "\n".join(gate_step)
    for required in (
        f"python {prefix}scripts/verify_release_legal_gate.py",
        f"--policy {prefix}legal/release-policy.json",
        f"--tag {tag}",
    ):
        _require(required in gate_text, f"{path} legal gate is missing: {required}")
    job_text = "\n".join("\n".join(step) for step in steps)
    _require(job_text.count("verify_release_legal_gate.py") == 1, f"{path} legal gate count changed")
    _require(job_text.count("release-policy.json") == 3, f"{path} release policy path count changed")
    _require(
        re.search(r"(?m)^\s+(?:continue-on-error|if|env)\s*:", gate_text) is None,
        f"{path} legal gate contains a YAML bypass",
    )
    for forbidden in (
        "||",
        "; exit 0",
        "continue-on-error",
        "SilentlyContinue",
        "-ErrorAction Ignore",
        "2>$null",
        "--allow",
        "ALLOW_RELEASE",
    ):
        _require(forbidden not in gate_text, f"{path} legal gate contains a bypass: {forbidden}")


def _verify_no_sensitive_literals(path: str, workflow: str) -> None:
    patterns = (
        (r"AIza[0-9A-Za-z_-]{30,}", "YouTube API key"),
        (r"(?:ghp_[0-9A-Za-z]{20,}|github_pat_[0-9A-Za-z_]{20,})", "GitHub token"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
        (r"(?im)^\s*(?:SID|SAPISID|HSID)\s*=", "cookie assignment"),
        (r"https?://[^\s]+googlevideo\.com[^\s]*", "signed media URL"),
        (r"(?i)[A-Z]:\\Users\\[^\\\r\n]+", "developer path"),
    )
    for pattern, label in patterns:
        _require(re.search(pattern, workflow) is None, f"{path} contains {label}")


def _test_current_owner_delegation() -> int:
    import verify_ffmpeg_provider_build_feasibility as provider_verifier

    relative = ".github/actions-pins.json"
    owner_smoke = "scripts/smoke_workflow_supply_chain.py"
    expected_delegations = {
        relative: owner_smoke,
        ".github/build-dependencies.json": "scripts/smoke_build_dependency_lock.py",
        "scripts/prepare_release_bundle.py": "scripts/smoke_release_bundle.py",
    }
    _require(relative in provider_verifier.PROTECTED, "action pins must remain protected")
    _require(
        provider_verifier.CURRENT_OWNER_SMOKES == expected_delegations,
        "FFmpeg provider current-owner delegation set differs",
    )

    provider_verifier._CURRENT_OWNER_CACHE.clear()
    with tempfile.TemporaryDirectory(prefix="s9h-action-owner-") as raw:
        root = Path(raw)
        protected = root / relative
        protected.parent.mkdir(parents=True, exist_ok=True)
        protected.write_bytes(b'{"schema_version":2}\n')
        smoke = root / owner_smoke
        smoke.parent.mkdir(parents=True, exist_ok=True)
        smoke.write_text("raise SystemExit(0)\n", encoding="utf-8", newline="\n")

        success = mock.Mock(returncode=0)
        with mock.patch.object(provider_verifier.subprocess, "run", return_value=success) as run:
            provider_verifier._verify_current_owner(root, relative, owner_smoke)
            provider_verifier._verify_current_owner(root, relative, owner_smoke)
            _require(run.call_count == 1, "successful current-owner result was not cached")
        _require(
            len(provider_verifier._CURRENT_OWNER_CACHE) == 1,
            "successful current-owner cache entry is missing",
        )

        provider_verifier._CURRENT_OWNER_CACHE.clear()
        failure = mock.Mock(returncode=1)
        with mock.patch.object(provider_verifier.subprocess, "run", return_value=failure):
            try:
                provider_verifier._verify_current_owner(root, relative, owner_smoke)
            except provider_verifier.FFmpegProviderBuildFeasibilityError as exc:
                _require(
                    "current owner gate failed" in str(exc),
                    "failing current-owner smoke error category differs",
                )
            else:
                raise SupplyChainContractError("failing current-owner smoke was accepted")
        _require(
            not provider_verifier._CURRENT_OWNER_CACHE,
            "failing current-owner result was cached",
        )

        with (
            mock.patch.object(provider_verifier, "PROTECTED", (relative,)),
            mock.patch.dict(provider_verifier.CURRENT_OWNER_SMOKES, {}, clear=True),
            mock.patch.object(provider_verifier.subprocess, "run", return_value=failure),
        ):
            try:
                provider_verifier._verify_protected(root, {})
            except provider_verifier.FFmpegProviderBuildFeasibilityError as exc:
                _require(
                    f"protected file changed: {relative}" in str(exc),
                    "missing delegation did not restore fail-closed baseline comparison",
                )
            else:
                raise SupplyChainContractError("missing current-owner delegation was accepted")
    provider_verifier._CURRENT_OWNER_CACHE.clear()
    return 2


def _test_r3a_negative_mutations(documents: dict[str, str], inventory: dict) -> int:
    current = CURRENT_CI_ACTIONS
    historical = HISTORICAL_ACTIONS
    ci = CI_WORKFLOW
    historical_workflow = ".github/workflows/release-v1.3.1.yml"
    cases: list[tuple[str, dict[str, str], dict]] = []

    mutated_inventory = copy.deepcopy(inventory)
    mutated_inventory["schema_version"] = 1
    cases.append(("malformed action pin schema", documents, mutated_inventory))

    for repository in (
        "actions/checkout",
        "actions/setup-python",
        "actions/upload-artifact",
        "actions/download-artifact",
    ):
        mutated = copy.deepcopy(documents)
        old = (
            f"{repository}@{current[repository]['commit']} "
            f"# {current[repository]['workflow_comment']}"
        )
        new = (
            f"{repository}@{historical[repository]['commit']} "
            f"# {historical[repository]['workflow_comment']}"
        )
        mutated[ci] = _replace_once(mutated[ci], old, new)
        cases.append((f"current CI reverts {repository}", mutated, inventory))

    mutated_inventory = copy.deepcopy(inventory)
    mutated_inventory["profiles"][CURRENT_PROFILE]["actions"]["actions/checkout"][
        "declared_runtime"
    ] = "node20"
    cases.append(("current CI inventory declares node20", documents, mutated_inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{current['actions/checkout']['commit']}",
        "actions/checkout@v6",
    )
    cases.append(("current CI uses mutable major tag", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        current["actions/checkout"]["commit"],
        current["actions/checkout"]["commit"][:7],
    )
    cases.append(("current CI uses short SHA", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"# {current['actions/checkout']['workflow_comment']}",
        "# v6.0.0",
    )
    cases.append(("semantic version comment differs", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        current["actions/checkout"]["commit"],
        "a" * 40,
    )
    cases.append(("action SHA differs from selected release", mutated, inventory))

    mutated_inventory = copy.deepcopy(inventory)
    mutated_inventory["workflow_profiles"][ci] = HISTORICAL_PROFILE
    cases.append(("current CI mapped to historical profile", documents, mutated_inventory))

    mutated_inventory = copy.deepcopy(inventory)
    mutated_inventory["workflow_profiles"][historical_workflow] = CURRENT_PROFILE
    cases.append(("historical workflow mapped to current profile", documents, mutated_inventory))

    mutated = copy.deepcopy(documents)
    mutated[historical_workflow] += "# historical byte mutation\n"
    cases.append(("historical workflow byte modified", mutated, inventory))

    mutated = copy.deepcopy(documents)
    old = (
        f"actions/checkout@{historical['actions/checkout']['commit']} "
        f"# {historical['actions/checkout']['workflow_comment']}"
    )
    new = (
        f"actions/checkout@{current['actions/checkout']['commit']} "
        f"# {current['actions/checkout']['workflow_comment']}"
    )
    _require(old in mutated[historical_workflow], "historical checkout mutation target missing")
    mutated[historical_workflow] = mutated[historical_workflow].replace(old, new, 1)
    cases.append(("historical pins silently replaced", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "          persist-credentials: false\n", "")
    cases.append(("persist-credentials omitted", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "          persist-credentials: false",
        "          persist-credentials: true",
    )
    cases.append(("persist-credentials true", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "          fetch-depth: 0", "          fetch-depth: 1")
    cases.append(("fetch-depth changes from zero", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "          artifact-ids: ${{ needs.windows-smoke.outputs.artifact-id }}",
        "          name: synthetic-release-bundle",
    )
    cases.append(("artifact ID selection replaced by name", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "          digest-mismatch: error\n", "")
    cases.append(("digest mismatch control removed", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "          digest-mismatch: error",
        "          digest-mismatch: ignore",
    )
    cases.append(("digest mismatch ignored", mutated, inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "actions/setup-python@",
        "unknown/setup-python@",
    )
    cases.append(("unknown action added", mutated, inventory))

    mutated_inventory = copy.deepcopy(inventory)
    del mutated_inventory["profiles"][CURRENT_PROFILE]["actions"]["actions/setup-python"]
    cases.append(("action missing from current profile", documents, mutated_inventory))

    mutated_inventory = copy.deepcopy(inventory)
    del mutated_inventory["profiles"][CURRENT_PROFILE]["actions"]["actions/checkout"][
        "declared_runtime"
    ]
    cases.append(("action.yml runtime evidence absent", documents, mutated_inventory))

    mutated_inventory = copy.deepcopy(inventory)
    mutated_inventory["profiles"][CURRENT_PROFILE]["actions"]["actions/checkout"][
        "release_tag"
    ] = "v6.1.0-rc.1"
    cases.append(("prerelease action tag recorded", documents, mutated_inventory))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "actions/checkout@", "third-party/checkout@")
    mutated_inventory = copy.deepcopy(inventory)
    third_party = mutated_inventory["profiles"][CURRENT_PROFILE]["actions"].pop(
        "actions/checkout"
    )
    third_party["repository"] = "third-party/checkout"
    mutated_inventory["profiles"][CURRENT_PROFILE]["actions"]["third-party/checkout"] = (
        third_party
    )
    cases.append(("third-party repository substituted", mutated, mutated_inventory))

    for label, mutated_documents, mutated_inventory in cases:
        _expect_failure(label, mutated_documents, mutated_inventory)
    return len(cases)


def _test_negative_mutations(documents: dict[str, str], inventory: dict) -> None:
    ci = CI_WORKFLOW
    release = ".github/workflows/release-v1.3.1.yml"
    current_actions = inventory["profiles"][CURRENT_PROFILE]["actions"]
    historical_actions = inventory["profiles"][HISTORICAL_PROFILE]["actions"]
    checkout_sha = current_actions["actions/checkout"]["commit"]
    setup_sha = current_actions["actions/setup-python"]["commit"]
    download_sha = current_actions["actions/download-artifact"]["commit"]
    historical_checkout_sha = historical_actions["actions/checkout"]["commit"]
    historical_upload_sha = historical_actions["actions/upload-artifact"]["commit"]
    historical_download_sha = historical_actions["actions/download-artifact"]["commit"]
    release_sha = historical_actions["softprops/action-gh-release"]["commit"]

    mutations = []

    mutated = copy.deepcopy(documents)
    mutated[release] = _remove_named_step(mutated[release], "Enforce fail-closed release legal gate")
    mutations.append(("missing release legal gate", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Enforce fail-closed release legal gate",
        "Install locked build dependencies",
    )
    mutations.append(("legal gate after dependency installation", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Enforce fail-closed release legal gate",
        "Build and validate checksum-pinned assets",
    )
    mutations.append(("legal gate after runtime acquisition and build", mutated))

    for label, old, new in (
        (
            "wrong legal gate tag",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.0",
        ),
        (
            "wrong legal gate policy",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1",
            "--policy source/legal/release-policy.json `\n            --tag v1.3.1",
        ),
        (
            "legal gate continue-on-error",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        continue-on-error: true\n",
        ),
        (
            "legal gate if false",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        if: false\n",
        ),
        (
            "legal gate environment bypass",
            "      - name: Enforce fail-closed release legal gate\n",
            "      - name: Enforce fail-closed release legal gate\n        env:\n          ALLOW_RELEASE: 1\n",
        ),
        (
            "legal gate shell suppression",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1 || exit 0",
        ),
        (
            "legal gate PowerShell suppression",
            "        run: |\n          python control/scripts/verify_release_legal_gate.py",
            "        run: |\n          $ErrorActionPreference = \"SilentlyContinue\"\n          python control/scripts/verify_release_legal_gate.py",
        ),
        (
            "legal gate allow argument",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1",
            "--policy control/legal/release-policy.json `\n            --tag v1.3.1 `\n            --allow",
        ),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(mutated[release], old, new)
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Enforce fail-closed release legal gate",
        "Validate immutable build outputs",
    )
    mutations.append(("legal gate only in publish job", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(mutated[release], "    needs: build\n", "")
    mutations.append(("publish no longer needs build", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _remove_named_step(
        mutated[release],
        "Prepare and verify release legal payload",
    )
    mutations.append(("missing release legal payload step", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Build and validate checksum-pinned assets",
        "Prepare and verify release legal payload",
    )
    mutations.append(("release legal payload before build", mutated))

    for label, old, new in (
        ("release bundle v1", "s9h-release-bundle-v2", "s9h-release-bundle-v1"),
        (
            "real publish verifier replaced by previous comment-only pattern",
            "          python control/scripts/prepare_release_bundle.py verify `",
            "          $requireReleaseReady = $true # --require-release-ready true",
        ),
        (
            "publish omits legal asset",
            "            release-bundle/assets/Youtube-Downloaderbs-v1.3.1-legal.zip\n",
            "",
        ),
        (
            "publish omits source asset",
            "            release-bundle/assets/Youtube-Downloaderbs-v1.3.1-aria2-source.zip\n",
            "",
        ),
        (
            "publish allows unmatched files",
            "          fail_on_unmatched_files: true",
            "          fail_on_unmatched_files: false",
        ),
        (
            "empty source placeholder",
            "      - name: Create and verify release bundle",
            "      - name: Create empty source placeholder\n"
            "        run: New-Item release/source-assets/empty.zip\n"
            "      - name: Create and verify release bundle",
        ),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(mutated[release], old, new)
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        "actions/checkout@v4",
    )
    mutations.append(("action major tag", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        "actions/checkout@" + checkout_sha[:7],
    )
    mutations.append(("short action SHA", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/download-artifact@{download_sha}",
        "actions/download-artifact@v4",
    )
    mutations.append(("mutable download action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "actions/download-artifact@",
        "unknown/download-artifact@",
    )
    mutations.append(("unknown download action owner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "actions/checkout@",
        "unknown/checkout@",
    )
    mutations.append(("unknown action owner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Check out workflow control source\n"
        f"        uses: actions/checkout@{historical_checkout_sha} # v4\n",
        "      - name: Check out workflow control source\n"
        f"        uses: actions/checkout@{'a' * 40} # v4\n",
    )
    mutations.append(("inconsistent action SHA", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "    runs-on: windows-2022\n    timeout-minutes: 30",
        "    runs-on: windows-latest\n    timeout-minutes: 30",
    )
    mutations.append(("mutable runner", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        'python-version: "3.11.9"',
        'python-version: "3.11"',
    )
    mutations.append(("broad Python selector", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "permissions:\n  contents: read",
        "permissions:\n  contents: write",
    )
    mutations.append(("top-level contents write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n",
        "",
    )
    mutations.append(("missing publish job write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: read",
        "  windows-smoke:\n    name: Windows compile, preflight and smoke\n    permissions:\n      contents: write",
    )
    mutations.append(("CI job write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n",
        "    permissions:\n      contents: write\n      id-token: write\n",
    )
    mutations.append(("id-token write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"uses: actions/checkout@{checkout_sha} # v6.1.0",
        "uses: ./.github/actions/unsafe",
    )
    mutations.append(("local action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/checkout@{checkout_sha}",
        f"owner/repo/.github/workflows/reuse.yml@{checkout_sha}",
    )
    mutations.append(("reusable workflow", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        f"actions/setup-python@{setup_sha} # v6.3.0",
        "docker://example.invalid/tool:latest",
    )
    mutations.append(("Docker action", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] += (
        "\n      - name: Legacy direct dependency install\n"
        "        run: python -m pip install --upgrade pip pyinstaller\n"
    )
    mutations.append(("legacy direct pip install", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] += (
        "\n      - name: Unpinned dependency install\n"
        "        run: python -m pip install pyinstaller\n"
    )
    mutations.append(("unpinned direct pip install", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Install locked build dependencies",
        "Build and validate checksum-pinned assets",
    )
    mutations.append(("installer after build", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _move_named_step_after(
        mutated[release],
        "Install locked build dependencies",
        "Check out release tag",
    )
    mutations.append(("installer after fixed-tag checkout", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _remove_named_step(
        mutated[release],
        "Check out workflow control source",
    )
    mutations.append(("missing control-source checkout", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: read\n    runs-on: windows-2022",
        "    permissions:\n      contents: write\n    runs-on: windows-2022",
    )
    mutations.append(("build job contents write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "    permissions:\n      contents: write\n    runs-on: windows-2022",
        "    permissions:\n      contents: write\n      packages: write\n    runs-on: windows-2022",
    )
    mutations.append(("publish job packages write", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Create and verify release bundle",
        f"      - uses: softprops/action-gh-release@{release_sha} # v2\n"
        "      - name: Create and verify release bundle",
    )
    mutations.append(("release action in build job", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Validate immutable build outputs",
        f"      - uses: actions/upload-artifact@{historical_upload_sha} # v4\n"
        "      - name: Validate immutable build outputs",
    )
    mutations.append(("upload action in publish job", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "      - name: Create and verify release bundle",
        f"      - uses: actions/download-artifact@{historical_download_sha} # v4\n"
        "      - name: Create and verify release bundle",
    )
    mutations.append(("download action in build job", mutated))

    for label, injected in (
        ("download from another repository", "          repository: other/repository\n"),
        ("download from another run", "          run-id: 123\n"),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(
            mutated[release],
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n" + injected,
        )
        mutations.append((label, mutated))

    for label, old, new in (
        ("missing merge-multiple", "          merge-multiple: true\n", ""),
        ("false merge-multiple", "          merge-multiple: true", "          merge-multiple: false"),
        ("quoted true merge-multiple", "          merge-multiple: true", '          merge-multiple: "true"'),
        ("quoted false merge-multiple", "          merge-multiple: true", '          merge-multiple: "false"'),
        (
            "download by artifact name",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n"
            "          name: release-bundle",
        ),
        (
            "download by artifact pattern",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}\n"
            "          pattern: release-*",
        ),
        (
            "multiple artifact IDs",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}",
            "          artifact-ids: ${{ needs.build.outputs.artifact-id }}, 123",
        ),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(mutated[release], old, new)
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        "          ref: v1.3.1\n",
        "          ref: v1.3.0\n",
    )
    mutations.append(("modified release tag ref", mutated))

    mutated = copy.deepcopy(documents)
    mutated[release] = _replace_once(
        mutated[release],
        '$env:RUNNER_TEMP\\s9h-build-venv-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT',
        ".build-venv",
    )
    mutations.append(("repository-contained build venv", mutated))

    for label, unsafe_argument in (
        ("alternate package index", "--extra-index-url https://example.invalid/simple"),
        ("trusted host", "--trusted-host example.invalid"),
        ("source distribution allowance", "--no-binary=:all:"),
    ):
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(
            mutated[release],
            '            --github-path "$env:GITHUB_PATH"\n',
            '            --github-path "$env:GITHUB_PATH" `\n'
            f"            {unsafe_argument}\n",
        )
        mutations.append((label, mutated))

    manual_trigger = "on:\n  workflow_dispatch:\n"
    trigger_mutations = (
        (
            "version workflow push trigger",
            "on:\n  workflow_dispatch:\n  push:\n    branches:\n      - main\n",
        ),
        (
            "version workflow self-path push trigger",
            "on:\n  workflow_dispatch:\n  push:\n    branches:\n      - main\n"
            "    paths:\n      - .github/workflows/release-v1.3.1.yml\n",
        ),
        (
            "version workflow schedule trigger",
            'on:\n  workflow_dispatch:\n  schedule:\n    - cron: "0 0 * * *"\n',
        ),
        (
            "version workflow pull request trigger",
            "on:\n  workflow_dispatch:\n  pull_request:\n",
        ),
        ("missing version workflow manual trigger", "on:\n"),
        (
            "version workflow pull request target trigger",
            "on:\n  workflow_dispatch:\n  pull_request_target:\n",
        ),
        (
            "version workflow inline pull request trigger",
            "on:\n  workflow_dispatch:\n  pull_request: {}\n",
        ),
    )
    for label, replacement in trigger_mutations:
        mutated = copy.deepcopy(documents)
        mutated[release] = _replace_once(mutated[release], manual_trigger, replacement)
        mutations.append((label, mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "  pull_request:\n", "")
    mutations.append(("CI missing pull request trigger", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(mutated[ci], "  push:\n", "")
    mutations.append(("CI missing push trigger", mutated))

    mutated = copy.deepcopy(documents)
    mutated[ci] = _replace_once(
        mutated[ci],
        "  pull_request:\n    branches:\n      - main\n",
        "  pull_request:\n    branches:\n      - develop\n",
    )
    mutations.append(("CI non-main branch filter", mutated))

    for label, mutated_documents in mutations:
        _expect_failure(label, mutated_documents, inventory)


def _expect_failure(label: str, documents: dict[str, str], inventory: dict) -> None:
    try:
        validate_supply_chain(documents, inventory)
    except SupplyChainContractError:
        return
    raise SupplyChainContractError(f"negative mutation was accepted: {label}")


def _mapping_block(lines: list[str], key: str, indent: int) -> list[str]:
    marker = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:\s*$")
    start = next((index for index, line in enumerate(lines) if marker.match(line)), None)
    _require(start is not None, f"mapping is missing: {key}")
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and _indent(line) <= indent:
            break
        block.append(line)
    return block


def _step_blocks(job: list[str]) -> list[list[str]]:
    starts = [
        index
        for index, line in enumerate(job)
        if _indent(line) == 6 and line.lstrip().startswith("- ")
    ]
    blocks = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(job)
        blocks.append(job[start:end])
    _require(bool(blocks), "release job contains no workflow steps")
    return blocks


def _step_name(step: list[str]) -> str | None:
    match = re.match(r"^\s*-\s+name:\s*(.*?)\s*$", step[0])
    return _unquote(match.group(1)) if match else None


def _named_step(steps: list[list[str]], name: str) -> list[str]:
    matches = [step for step in steps if _step_name(step) == name]
    _require(len(matches) == 1, f"workflow step must appear exactly once: {name}")
    return matches[0]


def _action_steps(steps: list[list[str]], action: str) -> list[list[str]]:
    pattern = re.compile(rf"\buses:\s*{re.escape(action)}@")
    return [step for step in steps if pattern.search("\n".join(step))]


def _scalar_value(lines: list[str], key: str, indent: int) -> str | None:
    pattern = re.compile(rf"^{' ' * indent}{re.escape(key)}\s*:\s*(.*?)\s*$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def _direct_mapping_pairs(lines: list[str], indent: int) -> list[tuple[str, str]]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$")
    return [
        (match.group(1), _unquote(match.group(2)))
        for line in lines
        if (match := pattern.match(line))
    ]


def _direct_mapping_keys(lines: list[str], indent: int) -> list[str]:
    pattern = re.compile(rf"^{' ' * indent}([A-Za-z0-9_-]+)\s*:.*$")
    return [match.group(1) for line in lines if (match := pattern.match(line))]


def _direct_sequence_values(lines: list[str], indent: int) -> list[str]:
    pattern = re.compile(rf"^{' ' * indent}-\s*(.*?)\s*$")
    return [
        _unquote(match.group(1))
        for line in lines
        if (match := pattern.match(line))
    ]


def _remove_named_step(workflow: str, name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    mutated, count = pattern.subn("", _normalize_newlines(workflow), count=1)
    _require(count == 1, f"step mutation target count is {count}: {name}")
    return mutated


def _move_named_step_after(workflow: str, name: str, target: str) -> str:
    normalized = _normalize_newlines(workflow)
    pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(name)}\s*$\n.*?(?=^      - |\Z)"
    )
    match = pattern.search(normalized)
    _require(match is not None, f"step mutation target is missing: {name}")
    block = match.group(0).rstrip("\n") + "\n"
    without = normalized[: match.start()] + normalized[match.end() :]
    target_pattern = re.compile(
        rf"(?ms)^      - name:\s*{re.escape(target)}\s*$\n.*?(?=^      - |\Z)"
    )
    target_match = target_pattern.search(without)
    _require(target_match is not None, f"step move target is missing: {target}")
    return without[: target_match.end()] + block + without[target_match.end() :]


def _replace_once(value: str, old: str, new: str) -> str:
    _require(value.count(old) == 1, f"mutation source is not unique: {old!r}")
    return value.replace(old, new, 1)


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _git_blob_sha1(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("ascii")
    return hashlib.sha1(header + value).hexdigest()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _indent(value: str) -> int:
    return len(value) - len(value.lstrip(" "))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SupplyChainContractError(message)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
