# Phase 6B2B2A Source-Kit Feasibility

## Purpose and fixed outcome

Phase 6B2B2A records an evidence-only inventory of source inputs for the pinned aria2 and FFmpeg binary packages. It evaluates what is known, what is only partially identified, and what evidence is still required. It does not assemble archives, approve source kits, change release policy, or make a legal conclusion.

The fixed outcome is fail-closed: legal compliance certification is false, source assets were not created, source kits remain not ready, assembly is not authorized, and release gate reconsideration is not allowed. The release remains blocked.

This document is not legal advice. Existing releases are not retroactively certified.

## Evidence hierarchy

The inventory applies this evidence hierarchy:

1. **Verified immutable upstream evidence** identifies an official repository and exact immutable content identity, backed by primary-source evidence. An independently established source-archive SHA-256 is retained where it already exists.
2. **Provider evidence** may identify a component name, linkage, build characteristic, or version. It remains partial when it does not identify the official upstream repository and exact immutable ref.
3. **Unresolved evidence** records a missing repository, ref, archive hash, version, toolchain detail, or build instruction explicitly. Unresolved does not mean absent.

Identified does not mean complete. A provider component list establishes what the provider says was enabled or linked, but it does not by itself prove an official upstream repository, exact source ref, complete archive, or build correspondence.

## Core source and full package inputs

The exact core source for aria2 and FFmpeg is backed by a repository, 40-character commit, and previously verified archive SHA-256 in `legal/source-correspondence.json`. Core source is only one input to each static binary package. Full package source inputs also include external static components, applicable system-facing components, exact toolchain details, configuration, build orchestration, and patch or explicit no-modification evidence.

PE imports only enumerate dynamic imports. They cannot enumerate code from static libraries already incorporated into a PE executable, so an empty non-system dynamic-import list cannot remove provider-evidenced static components from the inventory.

## aria2 matrix

| Input class | Count | Evidence state | Remaining gap |
| --- | ---: | --- | --- |
| Core source | 1 | Verified immutable evidence | None for the recorded core identity |
| External static components | 6 | Five verified immutable inputs; one partial primary-source record | Exact official GMP Mercurial changeset remains unresolved |
| External system-facing components | 0 | Not applicable | None |
| Toolchain | 1 record | Partial | Exact compiler and supporting-tool versions |
| Build orchestration | 1 record | Partial | Immutable provider script, exact configuration, patch status, and reproducible entrypoint |

The six external static components are `c-ares`, `expat`, `gmp`, `libssh2`, `sqlite`, and `zlib`. In the generated external-component resolution buckets, aria2 has `5` verified immutable inputs, `1` partially identified input, and `0` wholly unresolved records. The one partial record is GNU MP 6.3.0: its official archive was independently hashed, but an exact official Mercurial changeset was not established and the protected inventory contract accepts only a 40-character Git commit.

## Phase 6B2B2A1a — aria2 primary-source resolution

This evidence-only batch researched the six provider-identified static inputs through official project repositories and release infrastructure. The machine-readable results are in `legal/primary-source-evidence-aria2.json`. The exact count is **5 verified, 1 partial, 0 unresolved**.

| Component | Provider version | Official authority | Immutable identity status | Source archive hash status | Resolution status | Remaining blocker |
| --- | --- | --- | --- | --- | --- | --- |
| c-ares | 1.19.1 | `github.com/c-ares/c-ares` | Git commit `6360e96b5cf8e5980c887ce58ef727e53d77243a` | Independently hashed: `321700399b72ed0e037d0074c629e7741f6b2ec2dda92956abe3e9671d3e268e` | Verified immutable input | None for this upstream input |
| expat | 2.5.0 | `github.com/libexpat/libexpat` | Git commit `654d2de0da85662fcc7644a7acd7c2dd2cfb21f0` | Independently hashed: `ef2420f0232c087801abf705e89ae65f6257df6b7931d37846a193ef2e8cdcbe` | Verified immutable input | None for this upstream input |
| gmp | 6.3.0 | `gmplib.org` | Official Mercurial changeset unresolved | Independently hashed: `a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898` | Partial primary source | Exact official Mercurial changeset is unresolved; the protected inventory cannot represent it with a Git commit |
| libssh2 | 1.11.0 | `github.com/libssh2/libssh2` | Git commit `1c3f1b7da588f2652260285529ec3c1f1125eb4e` | Independently hashed: `a488a22625296342ddae862de1d59633e6d446eff8417398e06674a49be3d7c2` | Verified immutable input | None for this upstream input |
| sqlite | 3.43.1 | `sqlite.org` and official `github.com/sqlite/sqlite` mirror | Fossil check-in `2d3a40c05c49e1a49264912b1a05bc2143ac0e7c3df588276ce80a4cbc9bd1b0`, mapped to mirror commit `f1f6a0bba16895215150081e55dda0d960494773` | Independently hashed: `fe1bf29c5af379444ff5744f8317ad246fb865ceacc937903fe0fec0281fba2a` | Verified immutable input | None for this upstream input |
| zlib | 1.3 | `github.com/madler/zlib` | Git commit `09155eaa2f9270dc4ed1fa13e2b4b2613e6e4851` | Independently hashed: `8a9ba2898e1d0d774eca6ba5b4627a11e5588ba85c8851336eb38de4683050a7` | Verified immutable input | None for this upstream input |

Provider identification is not the same as immutable upstream resolution. Upstream resolution is not proof of exact provider build reproduction. Archive hashing does not prove the binary incorporated unmodified source. No source kit was assembled. Toolchain and build orchestration remain incomplete, source-kit assembly remains unauthorized, and publishing remains blocked. Existing releases are not retroactively certified.

This evidence assessment is not legal advice.

## FFmpeg matrix

| Input class | Count | Evidence state | Remaining gap |
| --- | ---: | --- | --- |
| Core source | 1 | Verified immutable evidence | None for the recorded core identity |
| External static components | 50 | Provider-identified names; partial | Versions, official repositories, immutable refs, and independent archive hashes |
| External system-facing components | 5 | Provider-identified candidates; partial | Exact role and source-input applicability remain unresolved |
| Toolchain | 1 record | Unresolved | Build host, compiler, compiler version, and supporting-tool versions |
| Build orchestration | 1 record | Partial | Immutable provider script, exact configuration, patch status, and reproducible entrypoint |

The five system-facing candidates are `d3d11va`, `d3d12va`, `dxva2`, `mediafoundation`, and `vaapi`. The generated external-component resolution buckets contain `0` verified immutable inputs, `55` partially identified inputs, and `0` wholly unresolved records. Every partial record still has unresolved version or upstream-identity fields; this classification preserves provider evidence without treating a name as complete source correspondence.

## Phase 6B2B2A1b - FFmpeg codec primary-source research

Sixteen FFmpeg codec-library names were reviewed against the exact Gyan FFmpeg 8.1.2 Essentials package evidence and official upstream project authorities. The Gyan package identifies the component names and static linkage, but exact provider dependency versions remain unresolved. Official upstream project identification does not prove provider use of a particular version, release, commit, or archive.

| Component | Provider version | Upstream project identified | Provider-to-upstream mapping | Resolution status | Blocking gap |
| --- | --- | --- | --- | --- | --- |
| `libaom` | unresolved | Alliance for Open Media AV1 codec | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libgsm` | unresolved | GSM 06.10 reference implementation | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libmp3lame` | unresolved | LAME | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libopencore-amrnb` | unresolved | opencore-amr | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libopencore-amrwb` | unresolved | opencore-amr | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libopenjpeg` | unresolved | OpenJPEG | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libopus` | unresolved | Opus | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libspeex` | unresolved | Speex | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libtheora` | unresolved | Theora | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libvo-amrwbenc` | unresolved | VisualOn AMR-WB encoder library | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libvorbis` | unresolved | Vorbis | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libvpx` | unresolved | WebM libvpx | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libwebp` | unresolved | WebP codec library | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libx264` | unresolved | x264 | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libx265` | unresolved | x265 | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libxvid` | unresolved | Xvid | unresolved | identified-name-only | Exact provider component version is unresolved. |

The evidence totals are:

- Provider versions verified: 0
- Verified immutable inputs: 0
- Identified-name-only inputs: 16
- Provider source archive hashes verified: 0

No component was promoted and the feasibility counts remain unchanged. No source archive hash was accepted as a provider input, no source archive was committed, and no source kit was assembled. The exact historical recipe, toolchain, configure command, and patch set remain unresolved.

The aria2 verifier owns aria2 semantics and shared gates; the FFmpeg codec batch verifier owns this 16-component evidence batch. Neither verifier weakens the general source-kit feasibility gate. Assembly remains unauthorized, publishing remains blocked, and existing releases are not retroactively certified. This is not legal advice.

## Phase 6B2B2A1c — FFmpeg graphics, subtitle, text and audio-support evidence

Fourteen FFmpeg support-library names were reviewed against the exact Gyan FFmpeg 8.1.2 Essentials package evidence and official upstream project authorities. Gyan identifies component names and static linkage, but exact provider dependency versions remain unresolved. Official upstream project identification does not prove provider use of a particular version, release, commit, or archive.

| Component ID | Functional category | Provider version | Provider-version status | Official upstream project identified | Provider-to-upstream mapping | Resolution status | Blocker summary |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cairo` | Graphics and 2D rendering | unresolved | unresolved | yes — Cairo | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `iconv` | Character-set conversion | unresolved | unresolved | yes — GNU libiconv | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libass` | Subtitle rendering | unresolved | unresolved | yes — libass | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libfontconfig` | Font discovery and configuration | unresolved | unresolved | yes — Fontconfig | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libfreetype` | Font rasterization | unresolved | unresolved | yes — FreeType | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libfribidi` | Bidirectional text processing | unresolved | unresolved | yes — GNU FriBidi | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libgme` | Game-music playback | unresolved | unresolved | yes — Game_Music_Emu | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libharfbuzz` | Text shaping | unresolved | unresolved | yes — HarfBuzz | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libopenmpt` | Tracker-music playback | unresolved | unresolved | yes — OpenMPT/libopenmpt | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `librubberband` | Audio time-stretching and pitch shifting | unresolved | unresolved | yes — Rubber Band Library | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libxml2` | XML processing | unresolved | unresolved | yes — libxml2 | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `libzimg` | Image scaling and colorspace conversion | unresolved | unresolved | yes — zimg | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `openal` | Audio output support | unresolved | unresolved | yes — OpenAL Soft | unresolved | identified-name-only | Exact provider component version is unresolved. |
| `sdl2` | Multimedia presentation support | unresolved | unresolved | yes — Simple DirectMedia Layer | unresolved | identified-name-only | Exact provider component version is unresolved. |

The evidence totals are:

- Provider versions verified: 0
- Verified immutable inputs: 0
- Identified-name-only inputs: 14
- Provider source archive hashes verified: 0

No component was promoted and the feasibility counts remain unchanged. No provider source archive hash was accepted, no source archive was committed, and no source kit was assembled. The exact historical recipe, exact toolchain, exact configure command, and patch set remain unresolved.

The aria2 verifier owns aria2 data and shared gates. The codec verifier owns the prior 16 codec records. The support verifier owns the new 14 support records. The general feasibility verifier owns overall schema and readiness. No verifier permanently freezes unrelated future evidence.

Assembly remains unauthorized, publishing remains blocked, and existing releases are not retroactively certified. This is not legal advice.

## Phase 6B2B2A1d — FFmpeg hardware acceleration and system-interface evidence

Provider metadata identifies names and linkage for 14 FFmpeg hardware, vendor SDK, toolkit and system-interface components. Exact toolkit, SDK and system-interface versions remain unresolved. Official vendor documentation does not prove historical provider version, provider incorporation, or an exact immutable input.

| Component ID | Linkage | Component nature | Official authority | Provider version | Provider-version status | Provider-to-official mapping | Source-kit treatment | Resolution status | Blocker summary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `amf` | static | vendor-sdk | Advanced Micro Devices, Inc. | unresolved | unresolved | unresolved | sdk-or-header-input-requires-version-resolution | identified-name-only | Exact provider component or SDK version is unresolved. |
| `cuda` | static | toolkit-interface | NVIDIA Corporation | unresolved | unresolved | unresolved | provider-label-needs-build-recipe | identified-name-only | Exact provider component or SDK version is unresolved. |
| `cuda-llvm` | static | toolkit-interface | LLVM Project | unresolved | unresolved | unresolved | provider-label-needs-build-recipe | identified-name-only | Exact provider component or SDK version is unresolved. |
| `cuvid` | static | hardware-api | NVIDIA Corporation | unresolved | unresolved | unresolved | sdk-or-header-input-requires-version-resolution | identified-name-only | Exact provider component or SDK version is unresolved. |
| `d3d11va` | system | system-api | Microsoft Corporation | unresolved | unresolved | unresolved | system-interface-documentation-only | system-component-candidate | Exact Windows SDK or system-interface version is unresolved. |
| `d3d12va` | system | system-api | Microsoft Corporation | unresolved | unresolved | unresolved | system-interface-documentation-only | system-component-candidate | Exact Windows SDK or system-interface version is unresolved. |
| `dxva2` | system | system-api | Microsoft Corporation | unresolved | unresolved | unresolved | system-interface-documentation-only | system-component-candidate | Exact Windows SDK or system-interface version is unresolved. |
| `ffnvcodec` | static | header-sdk | FFmpeg project | unresolved | unresolved | unresolved | sdk-or-header-input-requires-version-resolution | identified-name-only | Exact provider component or SDK version is unresolved. |
| `libmfx` | static | source-library | Intel Corporation | unresolved | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider component or SDK version is unresolved. |
| `libvpl` | static | source-library | Intel Corporation | unresolved | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider component or SDK version is unresolved. |
| `mediafoundation` | system | system-api | Microsoft Corporation | unresolved | unresolved | unresolved | system-interface-documentation-only | system-component-candidate | Exact Windows SDK or system-interface version is unresolved. |
| `nvdec` | static | hardware-api | NVIDIA Corporation | unresolved | unresolved | unresolved | provider-label-needs-build-recipe | identified-name-only | Exact provider component or SDK version is unresolved. |
| `nvenc` | static | hardware-api | NVIDIA Corporation | unresolved | unresolved | unresolved | provider-label-needs-build-recipe | identified-name-only | Exact provider component or SDK version is unresolved. |
| `vaapi` | system | system-api | libva project | unresolved | unresolved | unresolved | system-interface-documentation-only | system-component-candidate | Exact VA-API interface and implementation version is unresolved. |

The exact totals are:

- Total components: 14
- Static candidates: 9
- System candidates: 5
- Provider versions verified: 0
- Verified immutable inputs: 0
- Identified-name-only inputs: 9
- System-component candidates: 5
- Provider archive hashes verified: 0

No static candidate was promoted, and no system candidate was declared fully resolved. Component-nature and source-kit treatment are contextual classifications only. System APIs are not automatically conventional source archives, and provider linkage text alone does not prove a separate archive was statically incorporated.

No provider source archive hash was accepted. No source archive or SDK installer was committed. Feasibility counts remain unchanged, and no source kit was assembled. The exact historical recipe remains unresolved. The exact toolchain remains unresolved. The exact configure command remains unresolved. The patch set remains unresolved.

The aria2 verifier owns aria2 data and shared gates. The codec verifier owns the prior 16 codec records. The support verifier owns the prior 14 support records. The hardware/system verifier owns this 14-component batch. The general feasibility verifier owns overall schema and readiness. No component verifier permanently freezes unrelated future evidence.

Assembly remains unauthorized, publishing remains blocked, and existing releases are not retroactively certified. This is not legal advice.

## Phase 6B2B2A1e - FFmpeg compression, security, networking and remaining library evidence

Provider metadata identifies component names and static linkage for the final 11 FFmpeg external-library records. Exact provider dependency versions remain unresolved. Official upstream material supplies contextual project, interface and licensing evidence, but upstream project identification does not prove provider use and no component was promoted.

| Component ID | Functional class | Provider label interpretation | Official authority | Provider version | Provider-to-official mapping | Source-kit treatment | Resolution status | Blocker summary |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `avisynth` | scripting-integration | May identify an AviSynth integration API; the exact AviSynth or AviSynth+ implementation is unresolved. | AviSynth project | unresolved | unresolved | source-or-sdk-input-requires-version-resolution | identified-name-only | Exact implementation, API version, provider version and immutable input are unresolved. |
| `bzlib` | compression-library | Corresponds contextually to bzip2 compression support. | bzip2 project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact mapping from `bzlib` to a provider-selected bzip2 input is unresolved. |
| `gmp` | arithmetic-library | Corresponds contextually to GNU GMP; aria2 GMP evidence is not reused. | GNU MP project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Package-scoped FFmpeg GMP version and immutable input are unresolved. |
| `gnutls` | tls-crypto-library | Corresponds contextually to GnuTLS support. | GnuTLS project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected version and immutable input are unresolved. |
| `libsrt` | network-transport-library | Corresponds contextually to Secure Reliable Transport. | Haivision SRT project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected version and immutable input are unresolved. |
| `libssh` | secure-shell-library | Identifies libssh, not libssh2; aria2 libssh2 evidence is not reused. | libssh project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected libssh version and immutable input are unresolved. |
| `libvidstab` | video-processing-library | Corresponds contextually to vid.stab. | vid.stab project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected version and immutable input are unresolved. |
| `libvmaf` | quality-analysis-library | Corresponds contextually to VMAF and libvmaf. | Netflix VMAF project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected version and immutable input are unresolved. |
| `libzmq` | messaging-library | Corresponds contextually to ZeroMQ and libzmq. | ZeroMQ project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact provider-selected version and immutable input are unresolved. |
| `lzma` | compression-library | Corresponds contextually to liblzma and XZ compression support. | XZ Utils project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Exact liblzma or XZ provider version and immutable input are unresolved. |
| `zlib` | compression-library | Corresponds contextually to zlib; aria2 zlib evidence is not reused. | zlib project | unresolved | unresolved | source-archive-required-if-version-resolved | identified-name-only | Package-scoped FFmpeg zlib version and immutable input are unresolved. |

The exact batch totals are:

- Current batch components: 11
- Static components: 11
- Provider versions verified: 0
- Verified immutable inputs: 0
- Identified-name-only inputs: 11
- Provider source archive hashes verified: 0

Overall FFmpeg component coverage is:

- Codec batch: 16
- Support batch: 14
- Hardware/system batch: 14
- Remaining-library batch: 11
- Total dedicated component coverage: 55/55

Still unresolved:

- Provider versions complete: false
- Toolchain complete: false
- Build orchestration complete: false
- Source-kit completeness: false

No provider archive hash was accepted, no source archive was committed, and feasibility counts remain unchanged. FFmpeg GMP and zlib are distinct from package-scoped aria2 records. libssh is distinct from aria2 libssh2. All 55 FFmpeg external components now have dedicated evidence-batch coverage, but component-level coverage does not mean versions are resolved. Component-level coverage does not mean toolchain is resolved. Component-level coverage does not mean build orchestration is resolved. Component-level coverage does not establish source-kit completeness.

The aria2 verifier owns aria2 data and shared gates. The codec verifier owns the 16 codec records. The support verifier owns the 14 support records. The hardware/system verifier owns the 14 hardware/system records. The remaining-library verifier owns this 11-component batch. The general feasibility verifier owns overall schema and readiness. No component verifier permanently freezes unrelated future evidence.

No source kit was assembled. Assembly remains unauthorized, publishing remains blocked, and existing releases are not retroactively certified. This is not legal advice.

## Toolchain gaps

The aria2 provider metadata identifies an Ubuntu Linux mingw-w64 static cross-build, but it does not establish exact compiler and supporting-tool versions. The FFmpeg provider metadata identifies a 64-bit static GPLv3 package, but the build host, compiler, compiler version, and supporting tools remain unresolved.

## Build-orchestration gaps

Neither package has complete build orchestration tied to an immutable provider ref. Exact configuration, patch or explicit no-modification evidence, and a reproducible entrypoint remain unresolved. Provider configuration characteristics are useful partial evidence, not a substitute for those inputs.

## Phase boundary

The source input inventory is evidence-only. No source archive or source kit was created, and no source kit was assembled. Phase 6B2B2A does not authorize assembly or publishing.

Phase 6B2B2B is not authorized until every recorded blocker is resolved with primary-source evidence, including exact external-component identities, exact toolchain versions, complete immutable build orchestration, configuration, patch status, and source-archive manifests. A separate review and explicit authorization are required before any assembly work or release-gate reconsideration.
