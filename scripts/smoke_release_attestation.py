from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Callable

import smoke_ci_workflow as ci


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PIN_PATH = REPO_ROOT / ".github" / "actions-pins.json"
POLICY_PATH = REPO_ROOT / "legal" / "release-assurance-policy.json"
DOC_PATH = REPO_ROOT / "docs" / "release-attestations.md"
ATTEST_COMMIT = "f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6"
ATTEST_BLOB = "f3d593f3020cf14b65d2789e3788d015354475e9"
ATTEST_CONDITION = (
    "${{ github.event_name == 'push' || "
    "(github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name == github.repository) }}"
)
FORK_SKIP_CONDITION = (
    "${{ github.event_name == 'pull_request' && "
    "github.event.pull_request.head.repo.full_name != github.repository }}"
)
PROVENANCE_SUBJECTS = [
    "Youtube.Downloaderbs.exe",
    "Youtube-Downloaderbs-v0.0.0-ci.zip",
    "Youtube-Downloaderbs-v0.0.0-ci-legal.zip",
    "Youtube-Downloaderbs-v0.0.0-ci.spdx.json",
    "SHA256SUMS.txt",
    "RELEASE_MANIFEST.json",
]
SYNTHETIC_STATE = {
    "ci_integration_validated_remotely": True,
    "immutable_action_pin_integrated": True,
    "integration_implemented": True,
    "job_level_permission_design_implemented": True,
    "offline_bundle_verification_implemented": True,
    "online_verification_implemented": True,
}


class AttestationContractError(AssertionError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AttestationContractError(message)


def _replace_once(value: str, old: str, new: str) -> str:
    _require(value.count(old) == 1, f"mutation anchor count differs: {old}")
    return value.replace(old, new, 1)


def _replace_first(value: str, old: str, new: str) -> str:
    _require(old in value, f"mutation anchor is missing: {old}")
    return value.replace(old, new, 1)


def _subject_array(step_text: str) -> list[str]:
    match = re.search(
        r"(?ms)^\s*\$ExpectedSubjects\s*=\s*@\(\n(?P<body>.*?)^\s*\)\s*$",
        step_text,
    )
    _require(match is not None, "six-subject provenance array is missing")
    values = []
    for line in match.group("body").splitlines():
        item = re.fullmatch(r'\s*"([^"]+)",?\s*', line)
        _require(item is not None, "provenance subject array syntax is invalid")
        values.append(item.group(1))
    return values


def _action_steps(steps: list[list[str]]) -> list[list[str]]:
    return [
        step
        for step in steps
        if re.search(r"\buses:\s*actions/attest@", "\n".join(step))
    ]


def validate_attestation_contract(
    workflow: str,
    inventory: dict,
    policy: dict,
    documentation: str,
) -> None:
    workflow = ci._normalize_newlines(workflow)
    lines = workflow.splitlines()
    jobs = ci._mapping_block(lines, "jobs", 0)
    producer = ci._mapping_block(jobs, "windows-smoke", 2)
    handoff = ci._mapping_block(jobs, "release-bundle-handoff", 2)

    current_actions = inventory["profiles"]["current_ci"]["actions"]
    expected_pin = {
        "repository": "actions/attest",
        "release_tag": "v4.2.0",
        "workflow_comment": "v4.2.0",
        "commit": ATTEST_COMMIT,
        "declared_runtime": "node24",
        "official_repository": True,
        "action_yml_blob": ATTEST_BLOB,
        "lifecycle": "current",
        "occurrence_count": 2,
    }
    _require(
        current_actions.get("actions/attest") == expected_pin,
        "immutable actions/attest pin is invalid",
    )
    _require(
        inventory["workflow_action_counts"][".github/workflows/ci.yml"].get(
            "actions/attest"
        )
        == 2,
        "actions/attest workflow occurrence count is invalid",
    )

    _require(
        ci._direct_mapping_pairs(ci._mapping_block(lines, "permissions", 0), 2)
        == [("contents", "read")],
        "workflow-global permissions are not read-only",
    )
    _require(
        ci._direct_mapping_pairs(ci._mapping_block(producer, "permissions", 4), 6)
        == [("contents", "read")],
        "producer permissions are not read-only",
    )
    _require(
        ci._direct_mapping_pairs(ci._mapping_block(handoff, "permissions", 4), 6)
        == [
            ("contents", "read"),
            ("id-token", "write"),
            ("attestations", "write"),
        ],
        "attestation job permissions are not exact",
    )

    steps = ci._step_blocks(handoff)
    extractor = ci._named_step(steps, ci.RAW_EXTRACTION_STEP)
    semantic = ci._named_step(steps, "Verify synthetic release bundle handoff")
    fork_skip = ci._named_step(steps, "Report fork pull request attestation skip")
    subject_validation = ci._named_step(
        steps,
        "Validate synthetic attestation subjects",
    )
    provenance = ci._named_step(steps, "Attest synthetic provenance")
    sbom = ci._named_step(steps, "Attest synthetic SBOM")
    online = ci._named_step(steps, "Verify synthetic attestations online")
    offline = ci._named_step(steps, "Verify synthetic attestation bundles offline")
    _require(
        steps.index(extractor)
        < steps.index(semantic)
        < steps.index(subject_validation)
        < steps.index(provenance)
        < steps.index(sbom)
        < steps.index(online)
        < steps.index(offline),
        "attestation final-byte ordering is invalid",
    )
    _require(
        ci._scalar_value(fork_skip, "if", 8) == FORK_SKIP_CONDITION,
        "fork pull request skip condition is invalid",
    )
    for step in (subject_validation, provenance, sbom, online, offline):
        _require(
            ci._scalar_value(step, "if", 8) == ATTEST_CONDITION,
            "same-repository and main-push attestation condition is invalid",
        )
        _require(
            "continue-on-error" not in "\n".join(step),
            "attestation integration permits verification failure",
        )

    attest_steps = _action_steps(steps)
    _require(len(attest_steps) == 2, "actions/attest invocation count is invalid")
    for step in attest_steps:
        text = "\n".join(step)
        _require(
            f"uses: actions/attest@{ATTEST_COMMIT} # v4.2.0" in text,
            "actions/attest invocation is not the reviewed immutable pin",
        )
        _require("continue-on-error" not in text, "attestation action permits failure")

    provenance_with = ci._direct_mapping_pairs(
        ci._mapping_block(provenance, "with", 8),
        10,
    )
    _require(
        provenance_with
        == [
            (
                "subject-checksums",
                "${{ runner.temp }}/s9h-ci-attestation-${{ github.run_id }}-${{ github.run_attempt }}/provenance-subjects.sha256",
            ),
            ("create-storage-record", "false"),
            ("show-summary", "false"),
        ],
        "native provenance action inputs are invalid",
    )
    sbom_with = ci._direct_mapping_pairs(ci._mapping_block(sbom, "with", 8), 10)
    _require(
        sbom_with
        == [
            (
                "subject-path",
                "${{ github.workspace }}/release-bundle/assets/Youtube-Downloaderbs-v0.0.0-ci.zip",
            ),
            (
                "sbom-path",
                "${{ github.workspace }}/release-bundle/assets/Youtube-Downloaderbs-v0.0.0-ci.spdx.json",
            ),
            ("create-storage-record", "false"),
            ("show-summary", "false"),
        ],
        "native SPDX SBOM action inputs are invalid",
    )
    _require(
        all(
            key not in {"predicate", "predicate-path", "predicate-type"}
            for key, _ in provenance_with + sbom_with
        ),
        "custom predicate replaced a native attestation mode",
    )

    subject_text = "\n".join(subject_validation)
    _require(
        _subject_array(subject_text) == PROVENANCE_SUBJECTS,
        "six-subject provenance contract is invalid",
    )
    for required in (
        "Assert-BelowRoot $bundle $path",
        "$name -cne [IO.Path]::GetFileName($name)",
        "^[0-9a-f]{64}$",
        "Get-FileHash -LiteralPath $path -Algorithm SHA256",
        "$recordDigest -cne $actualDigest",
        "checksum record describes stale bytes",
        "duplicate or unexpected",
        "checksum subjects are incomplete",
        "[Text.UTF8Encoding]::new($false)",
    ):
        _require(required in subject_text, f"subject validation is missing: {required}")
    _require(
        "..\\RELEASE_MANIFEST.json" not in subject_text,
        "subject validation contains an unsafe path",
    )

    online_text = "\n".join(online)
    for required in (
        "gh --version",
        "gh attestation verify --help",
        '"sangbuidinh/s9h-YoutubeDownloader"',
        '"sangbuidinh/s9h-YoutubeDownloader/.github/workflows/ci.yml"',
        "--repo $repository",
        '--predicate-type "https://slsa.dev/provenance/v1"',
        '--predicate-type "https://spdx.dev/Document/v2.3"',
        "--source-digest $env:GITHUB_SHA",
        "--signer-workflow $signerWorkflow",
        "Online synthetic provenance verification failed",
        "Online synthetic SBOM verification failed",
    ):
        _require(required in online_text, f"online verification is missing: {required}")
    for control, count in (
        ("--repo $repository", 2),
        ('--predicate-type "https://slsa.dev/provenance/v1"', 1),
        ('--predicate-type "https://spdx.dev/Document/v2.3"', 1),
        ("--source-digest $env:GITHUB_SHA", 2),
        ("--signer-workflow $signerWorkflow", 2),
    ):
        _require(
            online_text.count(control) == count,
            f"online verification control count is invalid: {control}",
        )

    offline_text = "\n".join(offline)
    offline_env = ci._direct_mapping_pairs(ci._mapping_block(offline, "env", 8), 10)
    _require(
        offline_env
        == [
            (
                "PROVENANCE_BUNDLE",
                "${{ steps.attest-provenance.outputs.bundle-path }}",
            ),
            ("SBOM_BUNDLE", "${{ steps.attest-sbom.outputs.bundle-path }}"),
        ],
        "generated attestation bundle outputs are not exact",
    )
    for required in (
        "gh attestation trusted-root",
        "--bundle $env:PROVENANCE_BUNDLE",
        "--bundle $env:SBOM_BUNDLE",
        "--custom-trusted-root $trustedRootPath",
        "--repo $repository",
        '"http://127.0.0.1:9"',
        'SetEnvironmentVariable("GH_TOKEN", $null)',
        'SetEnvironmentVariable("GITHUB_TOKEN", $null)',
        "Offline synthetic provenance verification failed",
        "Offline synthetic SBOM verification failed",
    ):
        _require(required in offline_text, f"offline verification is missing: {required}")
    for control, count in (
        ("--repo $repository", 2),
        ("--bundle $env:PROVENANCE_BUNDLE", 1),
        ("--bundle $env:SBOM_BUNDLE", 1),
        ("--custom-trusted-root $trustedRootPath", 2),
        ('--predicate-type "https://slsa.dev/provenance/v1"', 1),
        ('--predicate-type "https://spdx.dev/Document/v2.3"', 1),
    ):
        _require(
            offline_text.count(control) == count,
            f"offline verification control count is invalid: {control}",
        )
    for forbidden in ("continue-on-error", "|| true", "SilentlyContinue", "gh api"):
        _require(
            forbidden.casefold() not in offline_text.casefold(),
            f"offline verification has a fallback or bypass: {forbidden}",
        )

    provenance_policy = policy["provenance"]
    _require(
        provenance_policy["synthetic_ci_integration"] == SYNTHETIC_STATE,
        "synthetic policy state is invalid",
    )
    for key in (
        "production_attestation_generated",
        "production_implementation_status",
        "production_readiness",
    ):
        _require(provenance_policy[key] is False, f"production policy claim is true: {key}")
    _require(
        all(value is False for value in policy["claims"].values()),
        "production assurance claim became true",
    )
    _require(
        all(
            value is False
            for value in policy["release_integration"][
                "existing_gate_invariants"
            ].values()
        ),
        "independent release gate became true",
    )
    for required in (
        "CI control evidence only",
        "not a production release",
        "SLSA level attainment",
        "Phase 7C-R2",
    ):
        _require(required in documentation, f"attestation documentation is missing: {required}")


Mutation = Callable[[dict], None]


def _workflow_replace(old: str, new: str) -> Mutation:
    def mutate(state: dict) -> None:
        state["workflow"] = _replace_once(state["workflow"], old, new)

    return mutate


def _workflow_replace_first(old: str, new: str) -> Mutation:
    def mutate(state: dict) -> None:
        state["workflow"] = _replace_first(state["workflow"], old, new)

    return mutate


def _inventory_set(path: tuple[str, ...], value: object) -> Mutation:
    def mutate(state: dict) -> None:
        target = state["inventory"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _policy_set(path: tuple[str, ...], value: object) -> Mutation:
    def mutate(state: dict) -> None:
        target = state["policy"]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _move_step_before(name: str, target: str) -> Mutation:
    def mutate(state: dict) -> None:
        state["workflow"] = ci._move_named_step_before(
            state["workflow"],
            name,
            target,
        )

    return mutate


def _run_negative(
    label: str,
    mutation: Mutation,
    expected: str,
    baseline: dict,
) -> None:
    state = copy.deepcopy(baseline)
    mutation(state)
    try:
        validate_attestation_contract(
            state["workflow"],
            state["inventory"],
            state["policy"],
            state["documentation"],
        )
    except AttestationContractError as exc:
        _require(
            expected.casefold() in str(exc).casefold(),
            f"{label}: expected {expected!r}, got {exc!r}",
        )
    else:
        raise AssertionError(f"{label}: mutation unexpectedly passed")
    print(f"PASS negative: {label}")


def main() -> int:
    baseline = {
        "workflow": WORKFLOW_PATH.read_text(encoding="utf-8"),
        "inventory": json.loads(PIN_PATH.read_text(encoding="utf-8")),
        "policy": json.loads(POLICY_PATH.read_text(encoding="utf-8")),
        "documentation": DOC_PATH.read_text(encoding="utf-8"),
    }
    validate_attestation_contract(
        baseline["workflow"],
        baseline["inventory"],
        baseline["policy"],
        baseline["documentation"],
    )
    positive_labels = [
        "immutable action pin",
        "job-scoped permissions",
        "six-subject provenance",
        "portable-ZIP SBOM subject",
        "online verification",
        "offline bundle verification",
        "same-repository PR condition",
        "main-push condition",
        "synthetic non-claims",
    ]
    for label in positive_labels:
        print(f"PASS positive: {label}")

    condition_line = f"        if: {ATTEST_CONDITION}"
    cases: list[tuple[str, Mutation, str]] = [
        (
            "mutable action tag",
            _workflow_replace_first(
                f"uses: actions/attest@{ATTEST_COMMIT} # v4.2.0",
                "uses: actions/attest@v4 # v4.2.0",
            ),
            "immutable pin",
        ),
        (
            "wrong action repository",
            _workflow_replace_first(
                f"uses: actions/attest@{ATTEST_COMMIT} # v4.2.0",
                f"uses: example/attest@{ATTEST_COMMIT} # v4.2.0",
            ),
            "invocation count",
        ),
        (
            "unreviewed action commit",
            _inventory_set(
                (
                    "profiles",
                    "current_ci",
                    "actions",
                    "actions/attest",
                    "commit",
                ),
                "0" * 40,
            ),
            "pin is invalid",
        ),
        (
            "global id-token permission",
            _workflow_replace(
                "permissions:\n  contents: read",
                "permissions:\n  contents: read\n  id-token: write",
            ),
            "workflow-global",
        ),
        (
            "global attestations permission",
            _workflow_replace(
                "permissions:\n  contents: read",
                "permissions:\n  contents: read\n  attestations: write",
            ),
            "workflow-global",
        ),
        (
            "producer attestation write permission",
            _workflow_replace(
                "      contents: read\n    runs-on: windows-2022\n    timeout-minutes: 30",
                "      contents: read\n      attestations: write\n    runs-on: windows-2022\n    timeout-minutes: 30",
            ),
            "producer permissions",
        ),
        (
            "contents write",
            _workflow_replace(
                "      contents: read\n      id-token: write\n      attestations: write",
                "      contents: write\n      id-token: write\n      attestations: write",
            ),
            "attestation job permissions",
        ),
        (
            "packages write",
            _workflow_replace(
                "      id-token: write\n      attestations: write",
                "      id-token: write\n      attestations: write\n      packages: write",
            ),
            "attestation job permissions",
        ),
        (
            "fork PR attempts attestation",
            _workflow_replace_first(condition_line, "        if: ${{ always() }}"),
            "condition",
        ),
        (
            "attestation before secure extraction",
            _move_step_before(
                "Validate synthetic attestation subjects",
                ci.RAW_EXTRACTION_STEP,
            ),
            "ordering",
        ),
        (
            "attestation before semantic verification",
            _move_step_before(
                "Validate synthetic attestation subjects",
                "Verify synthetic release bundle handoff",
            ),
            "ordering",
        ),
        (
            "stale checksum accepted",
            _workflow_replace(
                "if ($recordDigest -cne $actualDigest) {",
                "if ($false) {",
            ),
            "subject validation",
        ),
        (
            "missing subject",
            _workflow_replace(
                '              "SHA256SUMS.txt",\n',
                "",
            ),
            "six-subject",
        ),
        (
            "extra subject",
            _workflow_replace(
                '              "RELEASE_MANIFEST.json"\n',
                '              "RELEASE_MANIFEST.json",\n'
                '              "EXTRA.txt"\n',
            ),
            "six-subject",
        ),
        (
            "duplicate subject",
            _workflow_replace(
                '              "RELEASE_MANIFEST.json"\n',
                '              "SHA256SUMS.txt"\n',
            ),
            "six-subject",
        ),
        (
            "malformed digest accepted",
            _workflow_replace(
                'if ($digest -cnotmatch "^[0-9a-f]{64}$") {',
                'if ($digest -cnotmatch "^[0-9A-Fa-f]+$") {',
            ),
            "subject validation",
        ),
        (
            "unsafe subject path",
            _workflow_replace(
                '"RELEASE_MANIFEST.json" = Join-Path $bundle "RELEASE_MANIFEST.json"',
                '"RELEASE_MANIFEST.json" = Join-Path $bundle "..\\RELEASE_MANIFEST.json"',
            ),
            "unsafe path",
        ),
        (
            "production filename in synthetic subjects",
            _workflow_replace(
                '              "Youtube-Downloaderbs-v0.0.0-ci.zip",\n',
                '              "Youtube-Downloaderbs-v1.3.1.zip",\n',
            ),
            "six-subject",
        ),
        (
            "custom provenance predicate",
            _workflow_replace_first(
                "          create-storage-record: false\n          show-summary: false",
                "          predicate-type: https://example.invalid/custom\n"
                "          create-storage-record: false\n"
                "          show-summary: false",
            ),
            "native provenance",
        ),
        (
            "wrong SBOM subject",
            _workflow_replace(
                "subject-path: ${{ github.workspace }}/release-bundle/assets/Youtube-Downloaderbs-v0.0.0-ci.zip",
                "subject-path: ${{ github.workspace }}/release-bundle/assets/Youtube.Downloaderbs.exe",
            ),
            "native SPDX SBOM",
        ),
        (
            "wrong SBOM predicate type",
            _workflow_replace_first(
                '--predicate-type "https://spdx.dev/Document/v2.3"',
                '--predicate-type "https://example.invalid/spdx"',
            ),
            "online verification",
        ),
        (
            "online verification without repository",
            _workflow_replace(
                "                  --repo $repository `\n"
                '                  --predicate-type "https://slsa.dev/provenance/v1"',
                '                  --predicate-type "https://slsa.dev/provenance/v1"',
            ),
            "online verification",
        ),
        (
            "offline verification without bundle",
            _workflow_replace(
                "                      --bundle $env:PROVENANCE_BUNDLE `\n",
                "",
            ),
            "offline verification",
        ),
        (
            "offline verification without trusted root",
            _workflow_replace_first(
                "                      --custom-trusted-root $trustedRootPath `\n",
                "",
            ),
            "offline verification",
        ),
        (
            "verification fallback after failure",
            _workflow_replace(
                "      - name: Verify synthetic attestations online\n",
                "      - name: Verify synthetic attestations online\n"
                "        continue-on-error: true\n",
            ),
            "verification failure",
        ),
        (
            "production readiness true",
            _policy_set(("provenance", "production_readiness"), True),
            "production policy claim",
        ),
    ]
    for label, mutation, expected in cases:
        _run_negative(label, mutation, expected, baseline)

    print(
        "Release attestation smoke passed: "
        f"{len(positive_labels)} positive, {len(cases)} negative"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
