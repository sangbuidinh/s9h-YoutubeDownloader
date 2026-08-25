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

FFmpeg remains blocked. The exact provider media-autobuild-suite snapshot and
package-specific patch set are not proven, 18 of 50 direct external-version
entries remain unresolved, and the statically embedded transitive source
inventory is incomplete. The nearest public upstream snapshot is preserved only
as partial evidence, not asserted as the provider's exact build input. No FFmpeg
source asset was assembled.

## Release state

The gate, legal payload, and v2 release bundle now implement both blocked and
evidence-derived ready states. Ready v1.3.2 requires consistent policy and source
owner certifications, two exact verified source assets, an available legal
payload, zero blockers, and a release manifest derived from that validated state.
Historical releases remain blocked. Malformed or inconsistent state fails closed,
and no branch, user, environment, time, or force override exists.

The current v1.3.2 state remains blocked because FFmpeg source is incomplete and
the source assets are not integrated into the release workflow. The v2 contract
was retained; v3 was not selected because its additional SBOM path is not needed
to implement this narrower state correction.
