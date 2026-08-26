# v1.3.2 source-compliance implementation

This Phase 6B2 record is a technical source-compliance implementation, not legal
advice. It supersedes the execution restrictions of the earlier offline
consolidation phase without rewriting `legal/source-kit-readiness-consolidation.json`
or the historical evidence files on which that record depends.

## Classification model

- `REQUIRED_FOR_CORRESPONDING_SOURCE` covers actual corresponding source and the
  package-specific scripts or material needed to control compilation and
  installation.
- `REQUIRED_FOR_REPRODUCIBILITY_ASSURANCE` is additional evidence used to test a
  highly controlled rebuild; an unknown compiler hash or non-semantic timestamp
  is not, by itself, treated as a source-availability failure.
- `OPTIONAL_ASSURANCE` records useful provenance that is not an objective source
  prerequisite.
- `SYSTEM_LIBRARY_CANDIDATE` records a component that requires explicit system
  library treatment instead of automatic inclusion in a source archive.

This distinction follows the Corresponding Source and System Libraries terms in
the repository's preserved GPLv3 text and the official GNU GPLv3 publication.
General-purpose tools used unmodified and not part of the work are tracked as
assurance when useful, but they are not automatically copied into a source kit.
No byte-identical executable hash is required merely to establish source
availability.

## aria2

The current owner is `legal/source-compliance-v1.3.2.json`. Its immutable identity
schema supports Git commits, Mercurial changesets, Fossil IDs, and hashed release
archives. GNU MP 6.3.0 is therefore represented truthfully by the authoritative
GNU release archive, size, and SHA-256 instead of a fabricated Git commit.

The exact aria2 1.37.0 source release contains `Dockerfile.mingw`, `mingw-config`,
`mingw-release`, and `mingw-build-memo`. Together with the provider binary README,
these files identify the 64-bit MinGW build model and the six exact external
source archives: c-ares 1.19.1, Expat 2.5.0, GNU MP 6.3.0, libssh2 1.11.0,
SQLite 3.43.1, and zlib 1.3. The deterministic external source asset contains
those seven source archives, the four package-specific build files, a notice,
the aria2 license, and a canonical source manifest. It contains no executable.
`Youtube-Downloaderbs-v1.3.2-aria2-source.zip` has 14 entries, size 11,477,592
bytes, and SHA-256
`bb609dca9589eea96676a3d608652ffc24ea381cbfc19476dd6e582a95f2fd15`.

The 64-bit provider README distinguishes only the 32-bit build's additional
`--disable-ipv6` option. That is affirmative configuration-only evidence for the
64-bit package; it is not a claim that the binary is byte-for-byte reproducible.

## FFmpeg

The exact Gyan `ffmpeg-8.1.2-essentials_build.zip` package was verified at SHA-256
`db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`
before its metadata was used. Its README SHA-256 is
`9172433fb251059a58d2ff11ba8c6132e04819136ed96e809563911ff0d13816`.
The README identifies FFmpeg source commit
`38b88335f99e76ed89ff3c93f877fdefce736c13`, GPLv3 configuration, and 32 of
50 external-version entries. Static inspection of the verified executable bytes,
without execution, recovered the complete configure command, GCC 16.1.0 Rev2,
and UCRT64 build paths.

The five prior system-interface candidates are dispositioned as follows:

- `d3d11va`, `d3d12va`, `dxva2`, and `mediafoundation` remain Windows System
  Library candidates and are not automatically copied into a source archive.
- `vaapi` is not a Windows System Library; the provider reports version 2.24.0,
  so it is a source-required external input.

The continuation's bounded pass is recorded in
`legal/ffmpeg-correspondence-v1.3.2.json`. It retains all 55 legacy rows but corrects
their applicability against the exact configure string: 53 explicit component
enable tokens, including `fontconfig` (legacy `libfontconfig`), four Windows API
candidates, and the general-purpose `cuda-llvm` compilation path. Legacy `cuda`
and `libmfx` are not explicit enable tokens; that does not prove the absence of
automatically selected capabilities. The corrected source-required denominator
is 48, not 50. General-purpose compiler source is not automatically included.

24/48 direct source-required identities now have an exact upstream commit or
hashed release archive, independently measured archive size/SHA-256, and an
exact license-file location/hash. This is **base-source identity resolution**,
not provider patch completeness. The 32 README labels are not themselves 32
resolved immutable sources. `openal-soft latest` remains blocking; an embedded
1.25.2 string cannot identify its exact development revision. GnuTLS diagnostic
text is not accepted as version evidence. The binary identifies zlib-ng
development strings, so plain zlib is not substituted. SourceForge responses
for LAME and AMR were HTML, not archives, and were rejected. No provider binary
was executed. The AMF upstream SDK archive is research evidence, not an approved
redistributable source kit; its mixed contents require separate review.

Observed transitive clues include Expat, OpenSSL, nettle, pixman, libsodium and
libunibreak. Ogg and PNG feature dependencies require further linkage evidence.
This is an explicitly incomplete inventory, not a transitive closure. Version
strings do not prove patched immutable source inputs.

Microsoft's API documentation supports Windows System Library *candidacy* for
[D3D11 video](https://learn.microsoft.com/en-us/windows/win32/medfound/supporting-direct3d-11-video-decoding-in-media-foundation),
[D3D12 video](https://learn.microsoft.com/en-us/windows/win32/medfound/direct3d-12-video-overview),
[DXVA2](https://learn.microsoft.com/en-us/windows/win32/medfound/directx-video-acceleration-2-0),
and [Media Foundation](https://learn.microsoft.com/en-us/windows/win32/medfound/about-the-media-foundation-sdk).
It does not establish the exact redistributed SDK/header/runtime boundary or
certify an exclusion. [VAAPI on Windows](https://devblogs.microsoft.com/directx/video-acceleration-api-va-api-now-available-on-windows/)
still requires the external libva source; it is not excluded as an OS component.
System dispositions therefore remain incomplete (one external-source decision,
four uncertified candidates).

The [provider release](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2)
and release-period repository do not publish the complete recipe. The
[maintainer's explanation](https://github.com/GyanD/codexffmpeg/issues/211#issuecomment-3965744286)
describes a localized setup and recommends upstream MABS. Neither that statement,
the stale provider fork, the UCRT64 migration, nor the nearest public MABS
snapshot proves the exact provider-used snapshot. Historic patch discussion
does not prove patches or no patches in 8.1.2. Both snapshot and patch-set gates
remain false. The bounded Gyan path is closed as **INCOMPLETE**. No FFmpeg source
asset was assembled and no speculative searches or runtime migration follow.

## Fallback decision (owner choice required)

| Option | Consequence | Separate authority needed |
| --- | --- | --- |
| A. Retain Gyan and remain blocked | Preserve current runtime; no release-ready claim or publication | Complete missing provider evidence before reconsideration |
| B. Project-controlled pinned FFmpeg build | Establish owned source/patch/configuration provenance; changes the distributed runtime and requires behavior/security/license validation | Future controlled runtime migration, explicitly authorized |
| C. Stop redistributing FFmpeg | External acquisition changes installation, availability, trust and support contracts; does not automatically remove all obligations | Future architecture and runtime-acquisition design, explicitly authorized |

This run implements neither B nor C. Default remains A until the owner chooses.

## Release state

Policy states are `blocked`, `technical-ready`, and `authorized-ready`. The
technical owner must keep `legal_compliance_certified=false`, even when all
sources are ready. `legal/release-authorization-v1.3.2.json` independently records
`LEGAL_REVIEW_REQUIRED` with null decision/digests. A future explicit owner
decision must bind the release source commit and policy, source-owner, asset
contract and FFmpeg correspondence digests. No person, secret, branch, environment
or clock-based bypass exists. Production authorization remains absent.

The **prebuild** gate validates controls and complete correspondence but never
claims payload integration. The **final** gate requires exact source asset
names/sizes/hashes/manifests and calls the real legal-payload verifier with the
portable ZIP, legal ZIP, notes and source/control commits. That verifier checks
the precise legal file set, bytes, canonical manifest, checksums and portable
integration. An arbitrary nonempty ZIP is rejected. Only successful final
verification reports `release_payload_integrated=true`; publication additionally
requires explicit legal authorization and zero blockers. Synthetic authorized
fixtures are tests only. Historical policy records remain unchanged and blocked.
The source verifier also binds every declared identity to an embedded
`sources/<archive_filename>` entry with the exact size/hash, plus build, license
and notice evidence. A notice-only ZIP no longer qualifies as a source asset.
The accepted aria2 archive passes these checks without regeneration.

The current workflow installs locked dependencies before prebuild validation,
preserves annotated-tag/ancestry/release-absence checks, verifies both sources
before legal payload processing, runs final evidence validation before bundle
creation, and requires `--require-release-ready true` before upload and again
after immutable artifact download. Pinned public acquisition and FFmpeg assembly
are deliberately **not implemented** while correspondence is incomplete. An
unconditional unavailable-assembly guard prevents reaching downstream stages
even if prebuild controls are changed in isolation. Thus
`WORKFLOW_READY_STATE_WIRED=false`: gate ordering is repaired, but a complete
source-producing workflow is still blocked. No developer-local inputs or empty
source placeholders are accepted. The v2 asset contract is retained.

No production build, legal payload, release bundle, tag, dispatch or publication
was performed. Accepted release notes and runtime/transport behavior are unchanged.
