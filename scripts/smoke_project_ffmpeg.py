"""Offline fail-closed tests for the project-controlled FFmpeg build boundary."""
import copy
import hashlib
from pathlib import Path
import tempfile
import zipfile
from unittest import mock

import project_ffmpeg as runtime
import source_compliance
import prepare_source_kit
from verify_project_ffmpeg import validate_probe_output


def reject(action, label):
    try:
        action()
    except (runtime.ProjectFFmpegError, source_compliance.SourceComplianceError):
        return
    raise AssertionError(f"negative accepted: {label}")


def acquisition_case(responses, *, key="ffmpeg", expected=b"test", stale_partial=False):
    pin = copy.deepcopy(runtime.INPUTS[key])
    pin.update(filename="synthetic-source.tar.gz", size=len(expected),
               sha256=hashlib.sha256(expected).hexdigest())
    with tempfile.TemporaryDirectory(prefix="project-ffmpeg-acquire-") as temp:
        root = Path(temp)
        partial = root / (pin["filename"] + ".partial")
        final = root / pin["filename"]
        if stale_partial:
            partial.write_bytes(b"stale untrusted bytes")
        pending = list(responses)
        urls = []
        partial_absent_before_attempt = []

        def fake_run(argv, **_kwargs):
            if not pending:
                raise AssertionError("unexpected extra acquisition attempt")
            urls.append(argv[-1])
            partial_absent_before_attempt.append(not partial.exists())
            response = pending.pop(0)
            body = response.get("body")
            if body is not None:
                partial.write_bytes(body)
            return mock.Mock(returncode=response.get("returncode", 0),
                             stdout=response["stdout"], stderr=response.get("stderr", b""))

        with mock.patch.dict(runtime.INPUTS, {key: pin}), \
                mock.patch.object(runtime.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(runtime.time, "sleep") as sleep:
            error = None
            result = None
            try:
                result = runtime.acquire_input(key, root)
            except runtime.ProjectFFmpegError as exc:
                error = str(exc)
            return {
                "error": error,
                "result": result,
                "final_bytes": final.read_bytes() if final.exists() else None,
                "partial_exists": partial.exists(),
                "partial_absent_before_attempt": partial_absent_before_attempt,
                "urls": urls,
                "sleeps": [call.args[0] for call in sleep.call_args_list],
                "remaining": len(pending),
            }


def main():
    count = 0
    runtime.validate_configuration(runtime.CONFIGURE)
    for flag in ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265", "--enable-libopus"):
        reject(lambda flag=flag: runtime.validate_configuration([*runtime.CONFIGURE, flag]), flag)
        count += 1
    reject(lambda: runtime.validate_configuration([v for v in runtime.CONFIGURE if v != "--enable-libmp3lame"]), "MP3 encoder omitted")
    count += 1
    for kind, needed in runtime.REQUIRED_COMPONENTS.items():
        runtime.validate_components(kind, "\n".join(sorted(needed)))
        for component in needed:
            reject(lambda kind=kind, component=component: runtime.validate_components(kind, "\n".join(needed - {component})), f"{kind}/{component} missing")
            count += 1
    runtime.validate_dlls(["KERNEL32.dll", "msvcrt.dll"])
    reject(lambda: runtime.validate_dlls(["KERNEL32.dll", "libwinpthread-1.dll"]), "unexpected DLL")
    reject(lambda: runtime.validate_dlls([]), "missing dependency inspection")
    count += 2
    ffmpeg = runtime.INPUTS["ffmpeg"]
    runtime.validate_redirect("ffmpeg", ffmpeg["url"], f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{runtime.FFMPEG_COMMIT}")
    for target in (
        "http://codeload.github.com/FFmpeg/FFmpeg/tar.gz/" + runtime.FFMPEG_COMMIT,
        "https://example.invalid/ffmpeg.tar.gz",
        "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/master",
        f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{runtime.FFMPEG_COMMIT}?ref=latest",
    ):
        reject(lambda target=target: runtime.validate_redirect("ffmpeg", ffmpeg["url"], target), "source redirect/input drift")
        count += 1
    reject(lambda: runtime.validate_redirect("ffmpeg", "https://example.invalid/source", ffmpeg["url"]), "arbitrary initial input")
    reject(lambda: runtime.validate_redirect("lame", runtime.INPUTS["lame"]["url"], "https://example.invalid/lame.tar.gz"), "LAME redirect")
    count += 2
    redirect = f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{runtime.FFMPEG_COMMIT}"
    direct = acquisition_case([{"stdout": b"200\n", "body": b"test"}])
    assert direct["error"] is None and direct["final_bytes"] == b"test"
    assert direct["partial_absent_before_attempt"] == [True] and not direct["partial_exists"]
    lf_redirect = acquisition_case([
        {"stdout": b"302\n" + redirect.encode("ascii"), "body": b"untrusted redirect body"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert lf_redirect["error"] is None and lf_redirect["final_bytes"] == b"test"
    assert lf_redirect["urls"] == [ffmpeg["url"], redirect]
    crlf_redirect = acquisition_case([
        {"stdout": b"302\r\n" + redirect.encode("ascii") + b"\r\n", "body": b"redirect body"},
        {"stdout": b"200\r\n\r\n", "body": b"test"},
    ])
    assert crlf_redirect["error"] is None and crlf_redirect["final_bytes"] == b"test"
    transient_503 = acquisition_case([
        {"stdout": b"503\n", "body": b"temporary failure"},
        {"stdout": b"302\n" + redirect.encode("ascii"), "body": b"redirect body"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert transient_503["error"] is None and transient_503["sleeps"] == [2]
    assert transient_503["urls"] == [ffmpeg["url"], ffmpeg["url"], redirect]
    transient_429 = acquisition_case([
        {"stdout": b"429\n", "body": b"rate limited"},
        {"stdout": b"429\r\n\r\n", "body": b"rate limited again"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert transient_429["error"] is None and transient_429["sleeps"] == [2, 4]
    for status in ("404", "403"):
        permanent = acquisition_case([{
            "stdout": (status + "\nhttps://example.invalid/media?token=secret-canary").encode("ascii"),
            "body": b"untrusted error body", "stderr": b"Authorization: secret-canary",
        }])
        assert permanent["error"] is not None and f"status={status}" in permanent["error"]
        assert "secret-canary" not in permanent["error"]
        assert "example.invalid" not in permanent["error"] and "Authorization" not in permanent["error"]
        assert permanent["final_bytes"] is None and not permanent["partial_exists"]
        count += 1
    second_redirect = acquisition_case([
        {"stdout": b"302\n" + redirect.encode("ascii"), "body": b"first redirect body"},
        {"stdout": b"302\n" + redirect.encode("ascii"), "body": b"second redirect body"},
    ])
    assert second_redirect["error"] and "status=302 hop=2 retry=0" in second_redirect["error"]
    assert not second_redirect["partial_exists"]
    count += 1
    for invalid_redirect in (
        "https://example.invalid/FFmpeg/FFmpeg/tar.gz/" + runtime.FFMPEG_COMMIT,
        "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/master",
    ):
        invalid = acquisition_case([{
            "stdout": b"302\n" + invalid_redirect.encode("ascii"), "body": b"redirect body",
        }])
        assert invalid["error"] and invalid["final_bytes"] is None and not invalid["partial_exists"]
        count += 1
    wrong_size = acquisition_case([{"stdout": b"200\n", "body": b"longer"}])
    assert wrong_size["error"] == "download size/hash mismatch: ffmpeg"
    assert wrong_size["final_bytes"] is None and not wrong_size["partial_exists"]
    count += 1
    wrong_hash = acquisition_case([{"stdout": b"200\n", "body": b"best"}])
    assert wrong_hash["error"] == "download size/hash mismatch: ffmpeg"
    assert wrong_hash["final_bytes"] is None and not wrong_hash["partial_exists"]
    count += 1
    stale = acquisition_case([{"stdout": b"200\n", "body": b"test"}], stale_partial=True)
    assert stale["error"] is None and stale["partial_absent_before_attempt"] == [True]
    exhausted = acquisition_case([
        {"stdout": b"503\n", "body": b"failure 1"},
        {"stdout": b"503\n", "body": b"failure 2"},
        {"stdout": b"503\n", "body": b"failure 3"},
        {"stdout": b"503\n", "body": b"failure 4"},
    ])
    assert exhausted["error"] and "status=503 hop=1 retry=3" in exhausted["error"]
    assert exhausted["sleeps"] == [2, 4, 8] and not exhausted["partial_exists"]
    count += 1
    malformed = acquisition_case([{"stdout": b"302", "body": b"untrusted"}])
    assert malformed["error"] == "malformed HTTP response: ffmpeg hop=1 retry=0"
    assert not malformed["partial_exists"]
    count += 1

    sf_path = runtime.LAME_PROJECT_PATH
    sf_routing = runtime.LAME_SOURCE_URL

    def sf_gateway(query=""):
        return "https://downloads.sourceforge.net" + sf_path + (("?" + query) if query else "")

    def sf_mirror(query="", host="fixture.dl.sourceforge.net"):
        return "https://" + host + sf_path + (("?" + query) if query else "")

    def lame_case(responses, **kwargs):
        return acquisition_case(responses, key="lame", **kwargs)

    gateway_a1 = sf_gateway("r=&ts=opaque-a&use_mirror=fixture")
    mirror_m1 = sf_mirror("e=opaque-b&fid=opaque-c&st=opaque-d&viasf=1")
    routed = lame_case([
        {"stdout": b"302\n" + gateway_a1.encode("ascii"), "body": b"redirect bytes 1"},
        {"stdout": b"302\n" + mirror_m1.encode("ascii"), "body": b"redirect bytes 2"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert routed["error"] is None and routed["final_bytes"] == b"test"
    assert routed["partial_absent_before_attempt"] == [True, True, True]
    assert not routed["partial_exists"]
    direct_mirror = sf_mirror("e=changed-a&fid=changed-b&st=changed-c&viasf=1")
    direct_route = lame_case([
        {"stdout": b"302\n" + direct_mirror.encode("ascii"), "body": b"not final"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert direct_route["error"] is None and direct_route["final_bytes"] == b"test"
    viasf_only = lame_case([
        {"stdout": b"302\n" + sf_gateway("use_mirror=fixture-two").encode("ascii")},
        {"stdout": b"302\n" + sf_mirror("viasf=1").encode("ascii")},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert viasf_only["error"] is None
    empty_query = lame_case([
        {"stdout": b"302\n" + sf_gateway().encode("ascii")},
        {"stdout": b"302\n" + sf_mirror().encode("ascii")},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert empty_query["error"] is None
    changed_values = lame_case([
        {"stdout": b"302\n" + sf_gateway("r=changed-r&ts=changed-ts&use_mirror=changed-name").encode("ascii")},
        {"stdout": b"302\n" + sf_mirror("e=changed-e&fid=changed-fid&st=changed-st&viasf=1").encode("ascii")},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert changed_values["error"] is None
    sf_503 = lame_case([
        {"stdout": b"503\n", "body": b"temporary"},
        {"stdout": b"302\n" + direct_mirror.encode("ascii"), "body": b"not final"},
        {"stdout": b"200\n", "body": b"test"},
    ], stale_partial=True)
    assert sf_503["error"] is None and sf_503["sleeps"] == [2]
    assert sf_503["partial_absent_before_attempt"] == [True, True, True]
    sf_429 = lame_case([
        {"stdout": b"429\n", "body": b"temporary"},
        {"stdout": b"200\n", "body": b"test"},
    ])
    assert sf_429["error"] is None and sf_429["sleeps"] == [2]

    invalid_lame_targets = (
        "http://downloads.sourceforge.net" + sf_path,
        "https://fixture.dl.sourceforge.net.evil.example" + sf_path,
        "https://user@downloads.sourceforge.net" + sf_path,
        "https://user:password@downloads.sourceforge.net" + sf_path,
        "https://downloads.sourceforge.net:444" + sf_path,
        "https://downloads.sourceforge.net" + sf_path + "#fragment",
        "https://downloads.sourceforge.net/project/other/lame/3.100/lame-3.100.tar.gz",
        "https://downloads.sourceforge.net/project/lame/lame/3.99/lame-3.99.tar.gz",
        "https://downloads.sourceforge.net/project/lame/lame/3.100/other.tar.gz",
        "https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz/extra",
        sf_gateway("unknown=value"),
        sf_gateway("use_mirror=one&use_mirror=two"),
        sf_mirror("viasf=2"),
        sf_gateway("r=x&ts=y&use_mirror="),
        sf_gateway("r=x&ts=y&use_mirror=bad%2Fname"),
        sf_gateway("r=" + ("x" * 2050) + "&ts=y&use_mirror=fixture"),
        sf_gateway("r=" + ("x" * 4100) + "&ts=y&use_mirror=fixture"),
    )
    for target in invalid_lame_targets:
        invalid = lame_case([{"stdout": b"302\n" + target.encode("ascii"),
                              "body": b"untrusted redirect bytes"}])
        assert invalid["error"] and invalid["final_bytes"] is None
        assert not invalid["partial_exists"]
        count += 1

    mirror_one = sf_mirror("e=a&fid=b&st=c&viasf=1")
    gateway_two = sf_gateway("use_mirror=two")
    mirror_two = sf_mirror("viasf=1", host="second.dl.sourceforge.net")
    gateway_empty = sf_gateway()
    mirror_empty = sf_mirror(host="third.dl.sourceforge.net")
    fifth_redirect = lame_case([
        {"stdout": b"302\n" + mirror_one.encode("ascii")},
        {"stdout": b"302\n" + gateway_two.encode("ascii")},
        {"stdout": b"302\n" + mirror_two.encode("ascii")},
        {"stdout": b"302\n" + gateway_empty.encode("ascii")},
        {"stdout": b"302\n" + mirror_empty.encode("ascii")},
    ])
    assert fifth_redirect["error"] == "redirect limit exceeded: lame"
    assert not fifth_redirect["partial_exists"]
    count += 1
    looped = lame_case([
        {"stdout": b"302\n" + gateway_a1.encode("ascii")},
        {"stdout": b"302\n" + mirror_one.encode("ascii")},
        {"stdout": b"302\n" + gateway_a1.encode("ascii")},
    ])
    assert looped["error"] == "LAME redirect loop detected" and not looped["partial_exists"]
    count += 1
    lame_wrong_size = lame_case([{"stdout": b"200\n", "body": b"longer"}])
    assert lame_wrong_size["error"] == "download size/hash mismatch: lame"
    assert not lame_wrong_size["partial_exists"]
    count += 1
    lame_wrong_hash = lame_case([{"stdout": b"200\n", "body": b"best"}])
    assert lame_wrong_hash["error"] == "download size/hash mismatch: lame"
    assert not lame_wrong_hash["partial_exists"]
    count += 1
    final_redirect = lame_case([{
        "stdout": b"200\n" + direct_mirror.encode("ascii"), "body": b"test",
    }])
    assert final_redirect["error"] and "status=200" in final_redirect["error"]
    assert final_redirect["final_bytes"] is None and not final_redirect["partial_exists"]
    count += 1
    with tempfile.TemporaryDirectory(prefix="project-lame-initial-") as temp:
        altered = copy.deepcopy(runtime.INPUTS["lame"])
        altered["url"] = sf_routing + "?use_mirror=caller"
        with mock.patch.dict(runtime.INPUTS, {"lame": altered}), \
                mock.patch.object(runtime.subprocess, "run") as run:
            reject(lambda: runtime.acquire_input("lame", Path(temp)), "caller-modified LAME URL")
            assert not run.called
        count += 1
    opaque_canary = "opaque-routing-value-must-not-leak"
    leak = lame_case([{
        "stdout": ("302\n" + sf_gateway("unknown=" + opaque_canary)).encode("ascii"),
        "body": opaque_canary.encode("ascii"), "stderr": opaque_canary.encode("ascii"),
    }])
    assert leak["error"] and opaque_canary not in leak["error"]
    assert "unknown=" not in leak["error"] and not leak["partial_exists"]
    count += 1
    with tempfile.TemporaryDirectory(prefix="project-ffmpeg-pin-") as temp:
        pin = copy.deepcopy(ffmpeg)
        pin.update(filename="synthetic-source.tar.gz", size=4, sha256=hashlib.sha256(b"test").hexdigest())
        source = Path(temp) / pin["filename"]
        with mock.patch.dict(runtime.INPUTS, {"synthetic": pin}):
            source.write_bytes(b"test")
            runtime.verify_input(source, "synthetic")
            source.write_bytes(b"best")
            reject(lambda: runtime.verify_input(source, "synthetic"), "same-size source hash mismatch")
            source.write_bytes(b"longer")
            reject(lambda: runtime.verify_input(source, "synthetic"), "source size mismatch")
        count += 2
        reject(lambda: runtime.verify_input(source, "unknown"), "unknown source")
        count += 1
    for name in ("../outside", "/absolute", "C:/outside", "root\\outside"):
        reject(lambda name=name: runtime.safe_member(name), "archive traversal")
        count += 1
    for output in ('{}', '{"streams": [], "format": {"duration": "2"}}',
                   '{"streams": [{}], "format": {"duration": "2"}}',
                   '{"streams": [{"codec_name": "mp3", "codec_type": "audio"}], "format": {}}'):
        reject(lambda output=output: validate_probe_output(output), "missing ffprobe capability")
        count += 1
    with tempfile.TemporaryDirectory(prefix="project-source-determinism-") as temp:
        path = Path(temp) / "source.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo("NOTICE.txt", (2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = source_compliance.FIXED_FILE_MODE
            archive.writestr(info, b"synthetic fixture", compress_type=zipfile.ZIP_DEFLATED)
        reject(lambda: source_compliance._read_deterministic_zip(path, "synthetic"), "source-kit non-determinism")
        count += 1
        root = Path(__file__).resolve().parents[1]
        owner = source_compliance.load_owner(root / source_compliance.OWNER_PATH)
        if "runtime_build" in owner["kits"][1]:
            candidate = copy.deepcopy(owner)
            candidate["kits"][1]["runtime_build"]["binaries"][0]["sha256"] = "0" * 64
            (Path(temp) / "ffmpeg.exe").write_bytes(b"MZ unreviewed runtime")
            (Path(temp) / "ffprobe.exe").write_bytes(b"MZ unreviewed probe")
            reject(lambda: prepare_source_kit.verify_project_runtime(candidate, Path(temp), root), "runtime binary/source-owner mismatch")
            count += 1
            candidate = copy.deepcopy(owner)
            asset = candidate["kits"][1]["source_asset"]
            path = Path(temp) / asset["filename"]
            data = b"synthetic same-size unreviewed source kit"
            path.write_bytes(data)
            asset["size"] = len(data)
            reject(lambda: source_compliance.verify_source_asset(candidate, "ffmpeg", path), "source-kit SHA mismatch")
            count += 1
    print(f"Project FFmpeg source/configure/capability boundary passed: {count} negatives; no runtime executed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
