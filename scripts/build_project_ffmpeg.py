"""Build x64 FFmpeg/ffprobe from exact project-controlled sources on Windows."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import struct
import sys
import tarfile
import zipfile

import project_ffmpeg as contract


def build(work_root: Path, downloads: Path, jobs: int) -> dict:
    contract.require(os.name == "nt", "project FFmpeg build requires Windows x64")
    contract.require(struct.calcsize("P") == 8, "project FFmpeg controller must be x64")
    root = work_root.resolve()
    contract.require(len(root.parts) >= 2 and not re.search(r"\s|['\";$`]", str(root)), "use a dedicated build path without whitespace or shell metacharacters")
    contract.require(not root.exists(), "build root must be new; existing evidence is never overwritten")
    contract.require(1 <= jobs <= 32, "build concurrency out of bounds")
    recipes = {name: contract.file_identity(Path(__file__).with_name(name))
               for name in ("build_project_ffmpeg.py", "project_ffmpeg.py")}
    sources = {key: contract.acquire_input(key, downloads) for key in contract.INPUTS}
    root.mkdir(parents=True)
    logs = root / "logs"
    logs.mkdir()
    (root / "tmp").mkdir()
    tools = root / "tools"
    tools.mkdir()
    # The checksum-pinned upstream SFX is the extractor. No undocumented local 7-Zip.
    result = subprocess.run([str(sources["w64devkit"]), "-y", f"-o{tools}"], capture_output=True,
                            timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
    contract.require(result.returncode == 0, "pinned toolchain extraction failed")
    nasm_root = tools / "nasm"
    with zipfile.ZipFile(sources["nasm"]) as archive:
        for info in archive.infolist():
            contract.safe_member(info.filename)
            contract.require(not (info.external_attr >> 16 & 0o170000) == 0o120000, "assembler archive symlink")
        archive.extractall(nasm_root)
    tool_bin = tools / "w64devkit/bin"
    nasm_bin = nasm_root / "nasm-2.16.03"
    for tool in (tool_bin / "gcc.exe", tool_bin / "sh.exe", tool_bin / "make.exe", tool_bin / "objdump.exe", nasm_bin / "nasm.exe"):
        contract.require(tool.is_file(), f"pinned tool missing: {tool.name}")
    contract.extract_tar(sources["ffmpeg"], root / "sources/ffmpeg", "ffmpeg", f"FFmpeg-{contract.FFMPEG_COMMIT}")
    contract.extract_tar(sources["lame"], root / "sources/lame", "lame", "lame-3.100")
    contract.require((root / "sources/ffmpeg/RELEASE").read_text().strip() == "8.1.2", "source version mismatch")
    env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC", "TEMP", "TMP") if k in os.environ}
    env.update(PATH=os.pathsep.join([tool_bin.as_posix(), nasm_bin.as_posix(), (Path(os.environ["SystemRoot"]) / "System32").as_posix()]),
               LC_ALL="C", TZ="UTC", SOURCE_DATE_EPOCH="315532800", SHELL=(tool_bin / "sh.exe").as_posix(),
               PATH_SEPARATOR=";", ac_executable_extensions=".exe")
    env.update(TEMP=(root / "tmp").as_posix(), TMP=(root / "tmp").as_posix(), TMPDIR=(root / "tmp").as_posix())
    # GCC __FILE__ otherwise embeds the arbitrary out-of-tree build location.
    # This is compiler path normalization, not an upstream source patch.
    prefix_map = f"-ffile-prefix-map={root.as_posix()}=/project-ffmpeg-build"
    env["CFLAGS"] = prefix_map
    versions = {}
    for name, args in (("gcc", ["--version"]), ("make", ["--version"]), ("ld", ["--version"]),
                       ("nasm", ["-v"]), ("sh", ["--help"])):
        executable = (nasm_bin if name == "nasm" else tool_bin) / (name + ".exe")
        versions[name] = contract.run([str(executable), *args], cwd=root, env=env, log=logs / f"{name}-version.txt", timeout=30)
    for folder in ("build/lame", "build/ffmpeg", "prefix", "runtime"):
        (root / folder).mkdir(parents=True)
    prefix = (root / "prefix").as_posix()
    lame_args = ["../../sources/lame/configure", f"--prefix={prefix}", "--build=x86_64-w64-mingw32", "--host=x86_64-w64-mingw32",
                 "--disable-shared", "--enable-static", "--disable-frontend", "--disable-decoder",
                 "--disable-nasm", "--disable-dependency-tracking"]
    lame_env = dict(env, CFLAGS=f"-O2 -std=gnu99 {prefix_map}", LDFLAGS="-static -Wl,--no-insert-timestamp")
    contract.run([str(tool_bin / "sh.exe"), *lame_args], cwd=root / "build/lame", env=lame_env, log=logs / "lame-configure.txt")
    contract.run([str(tool_bin / "make.exe"), f"-j{jobs}"], cwd=root / "build/lame", env=lame_env, log=logs / "lame-make.txt")
    contract.run([str(tool_bin / "make.exe"), "install"], cwd=root / "build/lame", env=lame_env, log=logs / "lame-install.txt")
    contract.validate_configuration(contract.CONFIGURE)
    ffmpeg_args = ["../../sources/ffmpeg/configure", *contract.CONFIGURE]
    contract.run([str(tool_bin / "sh.exe"), *ffmpeg_args], cwd=root / "build/ffmpeg", env=env, log=logs / "ffmpeg-configure.txt")
    contract.run([str(tool_bin / "make.exe"), f"-j{jobs}", "V=1", "ffmpeg.exe", "ffprobe.exe"], cwd=root / "build/ffmpeg", env=env, log=logs / "ffmpeg-make.txt")
    binaries = []
    for name in ("ffmpeg", "ffprobe"):
        binary = root / f"runtime/{name}.exe"
        shutil.copyfile(root / f"build/ffmpeg/{name}.exe", binary)
        version = contract.run([str(binary), "-version"], cwd=root, env=env, log=logs / f"{name}-version.txt", timeout=30)
        contract.require(version.startswith(f"{name} version 8.1.2-s9h-minimal-1 "), "built runtime version mismatch")
        buildconf = contract.run([str(binary), "-buildconf"], cwd=root, env=env, log=logs / f"{name}-buildconf.txt", timeout=30)
        flags = next(line.split("configuration: ", 1)[1] for line in version.splitlines() if "configuration: " in line)
        contract.validate_configuration(shlex.split(flags))
        imports = contract.run([str(tool_bin / "objdump.exe"), "-p", str(binary)], cwd=root, env=env, log=logs / f"{name}-imports.txt", timeout=30)
        contract.require("pei-x86-64" in imports, "runtime is not x64 PE")
        dlls = re.findall(r"DLL Name:\s*(\S+)", imports)
        contract.validate_dlls(dlls)
        binaries.append({**contract.file_identity(binary), "dlls": sorted(dlls)})
    ffmpeg = root / "runtime/ffmpeg.exe"
    for kind in contract.REQUIRED_COMPONENTS:
        output = contract.run([str(ffmpeg), "-hide_banner", "-" + kind], cwd=root, env=env, log=logs / f"{kind}.txt", timeout=30)
        contract.validate_components(kind, output)
    contract.require(recipes == {name: contract.file_identity(Path(__file__).with_name(name)) for name in recipes},
                     "build recipe changed during execution")
    result = {"schema_version": 1, "runtime": "project-controlled", "version": "8.1.2",
              "inputs": contract.INPUTS, "configure": contract.CONFIGURE, "external_libraries": contract.EXTERNAL_LIBRARIES,
              "source_patches": [], "binaries": binaries,
              "tool_versions": versions, "recipe_files": recipes, "lame_configure": lame_args,
              "environment": env, "legal_release_authorized": False,
              "non_claims": ["byte-identical-rebuild", "legal-advice", "reproducible-build"]}
    (root / "BUILD_MANIFEST.json").write_bytes(contract.canonical(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--downloads-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    try:
        result = build(args.work_root, args.downloads_root, args.jobs)
        print(contract.canonical({"binaries": result["binaries"], "result": "BUILT_NOT_AUTHORIZED"}).decode(), end="")
    except (contract.ProjectFFmpegError, OSError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"Project FFmpeg build error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
