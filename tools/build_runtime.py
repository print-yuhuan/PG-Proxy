"""Build the bundled WAA worker offline for Android and optionally Windows.

The Android output is an executable PIE named libpgwaa.so so Android's native
library packaging installs it in nativeLibraryDir. It is not a JNI library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
ABIS = {
    "arm64-v8a": ("arm64", "aarch64-linux-android", 183),
    "x86_64": ("amd64", "x86_64-linux-android", 62),
}
CACHE_SCHEMA = 2
ANDROID_API = 28
BUILD_SCRIPTS = ("tools/build_runtime.py", "tools/build_app.py")
GO_BUILD_FLAGS = ["-mod=vendor", "-trimpath", "-buildvcs=false"]
ANDROID_LDFLAGS = "-s -w -buildid= -linkmode=external -extldflags=-Wl,-z,max-page-size=16384,-z,common-page-size=16384"
COMMON_BUILD_ENV = {
    "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local", "GOFLAGS": "",
    "GOWORK": "off", "GOENV": "off", "GOEXPERIMENT": "", "GOAMD64": "v1", "GOARM64": "v8.0",
    "CGO_CFLAGS": "-O2 -g", "CGO_CPPFLAGS": "", "CGO_CXXFLAGS": "-O2 -g", "CGO_LDFLAGS": "-O2 -g",
}


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_snapshot(root: Path) -> dict:
    """Hash actual inputs, including untracked sources, vendored code and embeds.

    Do not use git ls-files: a newly added Go/JS/data file can affect compilation
    before it is staged. Runtime has no in-tree build output, so hash every file.
    Reject links rather than following inputs outside the declared source tree.
    """
    root = root.resolve()
    runtime = root / "runtime"
    if not runtime.is_dir() or runtime.is_symlink() or runtime.resolve() != runtime:
        raise RuntimeError("Native source directory is missing or is a link")
    files = []
    def walk_error(error: OSError) -> None:
        raise RuntimeError("Cannot enumerate all native source inputs") from error
    for directory, directories, names in os.walk(runtime, followlinks=False, onerror=walk_error):
        for name in directories:
            child = Path(directory) / name
            if child.is_symlink() or child.resolve() != child:
                raise RuntimeError("Native source directory links are not supported")
        files.extend(Path(directory) / name for name in names)
    files.extend(root / name for name in BUILD_SCRIPTS)
    records = []
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise RuntimeError("Native source input is missing or is not a regular file")
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
            raise RuntimeError("Native source changed while hashing; retry after editing finishes")
        records.append({"path": path.relative_to(root).as_posix(), "bytes": after.st_size, "sha256": digest})
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": records}


def require_unchanged_sources(root: Path, before: dict) -> None:
    if source_snapshot(root) != before:
        raise RuntimeError("Native source changed during the build; rebuild WAA after editing finishes")


def android_build_config(abi: str, api: int = ANDROID_API) -> dict:
    arch, target, _ = ABIS[abi]
    return {
        "api": api, "compiler_target": f"{target}{api}", "build_mode": "pie",
        "go_flags": GO_BUILD_FLAGS, "ldflags": ANDROID_LDFLAGS,
        "environment": dict(COMMON_BUILD_ENV, GOOS="android", GOARCH=arch, CGO_ENABLED="1"),
    }


def validate_toolchain(toolchain: object) -> None:
    if not isinstance(toolchain, dict):
        raise RuntimeError("Native cache lacks recorded toolchain versions")
    go = re.fullmatch(r"go version go(\d+)\.(\d+)(?:\.[0-9]+)?[^ ]* [^ ]+", str(toolchain.get("go", "")))
    ndk = re.fullmatch(r"(\d+)(?:\.[0-9]+)+", str(toolchain.get("ndk", "")))
    if (not go or tuple(map(int, go.group(1, 2))) < (1, 27)
            or not ndk or int(ndk.group(1)) < 28 or not toolchain.get("clang")):
        raise RuntimeError("Native cache requires recorded Go 1.27+, NDK r28+ and Clang versions")


def validate_cached_artifact(root: Path, record: object, abi: str, api: int = ANDROID_API) -> dict:
    if not isinstance(record, dict) or record.get("abi") != abi:
        raise RuntimeError(f"Native cache is missing {abi}; rebuild that ABI")
    expected_path = f"app/src/main/jniLibs/{abi}/libpgwaa.so"
    if record.get("path") != expected_path or record.get("api") != api:
        raise RuntimeError(f"Native cache has the wrong path/API for {abi}; rebuild that ABI")
    if record.get("build_config") != android_build_config(abi, api):
        raise RuntimeError(f"Native cache build configuration differs for {abi}; rebuild that ABI")
    validate_toolchain(record.get("toolchain"))
    path = root / expected_path
    if (not path.is_file() or path.is_symlink() or path.resolve() != path.absolute()
            or record.get("bytes") != path.stat().st_size or record.get("sha256") != file_sha256(path)):
        raise RuntimeError(f"Native worker checksum/size differs for {abi}; rebuild that ABI")
    return record


def read_cache_manifest(root: Path) -> dict:
    try:
        manifest = json.loads((root / "artifacts/runtime/manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("Native cache manifest is missing or unreadable; run tools/build_runtime.py first") from None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != CACHE_SCHEMA
            or not isinstance(manifest.get("source"), dict) or not isinstance(manifest.get("artifacts"), list)):
        raise RuntimeError("Native cache has no supported source provenance; rebuild WAA instead of skipping it")
    return manifest


def verify_native_cache(root: Path, abis: list[str], api: int = ANDROID_API) -> dict:
    """Fail closed before using a cached worker or publishing an existing APK."""
    root = root.resolve()
    manifest = read_cache_manifest(root)
    if manifest["source"] != source_snapshot(root):
        raise RuntimeError("Native cache source digest differs from the current tree; rebuild WAA without --skip-native-build")
    records = {}
    for abi in abis:
        matches = [record for record in manifest["artifacts"] if isinstance(record, dict) and record.get("abi") == abi]
        if len(matches) != 1:
            raise RuntimeError(f"Native cache must contain exactly one {abi} artifact; rebuild that ABI")
        records[abi] = validate_cached_artifact(root, matches[0], abi, api)
    # Also catches edits made while the worker bytes were being checked.
    require_unchanged_sources(root, manifest["source"])
    return {"source_sha256": manifest["source"]["sha256"], "artifacts": records}


def reusable_artifacts(root: Path, snapshot: dict) -> dict:
    """Keep other ABIs only when their current bytes and provenance still match."""
    try:
        manifest = read_cache_manifest(root)
    except RuntimeError:
        return {}
    if manifest["source"] != snapshot:
        return {}
    records = {}
    for abi in ABIS:
        matches = [record for record in manifest["artifacts"] if isinstance(record, dict) and record.get("abi") == abi]
        if len(matches) != 1:
            continue
        record = matches[0]
        try:
            records[abi] = validate_cached_artifact(root, record, abi)
        except RuntimeError:
            continue
    return records


def write_cache_manifest(root: Path, snapshot: dict, results: dict, toolchain: dict) -> None:
    require_unchanged_sources(root, snapshot)
    manifest = {"schema_version": CACHE_SCHEMA, "source": snapshot, **toolchain, "offline_modules": True,
                "android_app_execution_verified": False, "artifacts": list(results.values())}
    destination = root / "artifacts/runtime/manifest.json"
    temporary = destination.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Hash again after serialization so inputs changed before commit fail.
        require_unchanged_sources(root, snapshot)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def elf_metadata(path: Path, expected_machine: int) -> dict:
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise RuntimeError("Expected a little-endian ELF64 executable")
    header = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    kind, machine, _, entry, phoff = header[:5]
    phentsize, phnum = header[8:10]
    loads = []
    interpreter = None
    for index in range(phnum):
        ptype, _, offset, address, _, size, _, alignment = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + index * phentsize
        )
        if ptype == 1:
            if alignment < 16384 or (address - offset) % 16384:
                raise RuntimeError("ELF LOAD segment is not aligned for 16 KB pages")
            loads.append(alignment)
        elif ptype == 3:
            interpreter = data[offset:offset + size].rstrip(b"\0").decode("ascii")
    if kind != 3 or machine != expected_machine or not entry or not loads:
        raise RuntimeError("Expected a PIE executable for the requested Android ABI")
    if interpreter != "/system/bin/linker64":
        raise RuntimeError(f"Unexpected ELF interpreter: {interpreter!r}")
    return {
        "type": "ET_DYN executable PIE",
        "machine": machine,
        "interpreter": interpreter,
        "load_segment_alignment": loads,
        "sixteen_kb_elf_aligned": True,
    }


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        command, cwd=ROOT / "runtime", env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default=shutil.which("go"), help="Go 1.27+ executable")
    parser.add_argument("--ndk", default=os.environ.get("ANDROID_NDK_HOME"), help="Android NDK directory (r28+)")
    parser.add_argument("--abi", action="append", choices=ABIS, help="Repeat to select ABIs; default both")
    parser.add_argument("--api", type=int, default=ANDROID_API)
    parser.add_argument("--windows", action="store_true", help="Also compile a Windows x86_64 test worker")
    args = parser.parse_args()
    if not args.go or not args.ndk:
        parser.error("Supply --go and --ndk, or set PATH and ANDROID_NDK_HOME")
    if args.api < 28:
        parser.error("This project targets Android API 28 or later")
    host = {"Windows": "windows-x86_64", "Linux": "linux-x86_64", "Darwin": "darwin-x86_64"}[platform.system()]
    suffix = ".exe" if os.name == "nt" else ""
    tool_bin = Path(args.ndk).resolve() / "toolchains/llvm/prebuilt" / host / "bin"
    clang = tool_bin / ("clang" + suffix)
    readelf = tool_bin / ("llvm-readelf" + suffix)
    if not clang.is_file() or not readelf.is_file():
        parser.error("The supplied NDK does not contain the expected host LLVM tools")
    output = ROOT / "artifacts/runtime"
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, **COMMON_BUILD_ENV)
    go_version = run([args.go, "version"], env=env)
    ndk_version = next(
        (line.split("=", 1)[1].strip() for line in (Path(args.ndk) / "source.properties").read_text().splitlines()
         if line.startswith("Pkg.Revision")), "unknown"
    )
    toolchain = {"go": go_version, "ndk": ndk_version, "clang": run([str(clang), "--version"], env=env)}
    validate_toolchain(toolchain)
    snapshot = source_snapshot(ROOT)
    results = reusable_artifacts(ROOT, snapshot)
    for abi in args.abi or list(ABIS):
        _, target, machine = ABIS[abi]
        binary = ROOT / "app/src/main/jniLibs" / abi / "libpgwaa.so"
        binary.parent.mkdir(parents=True, exist_ok=True)
        config = android_build_config(abi, args.api)
        build_env = dict(env, **config["environment"], CC=f'"{clang}" --target={target}{args.api}')
        temporary_binary = binary.with_suffix(".so.building")
        print(f"Building Android {abi}, API {args.api} ...", flush=True)
        try:
            run([args.go, "build", *GO_BUILD_FLAGS, "-buildmode=pie",
                 "-ldflags", config["ldflags"], "-o", str(temporary_binary), "."], env=build_env)
            elf = elf_metadata(temporary_binary, machine)
            report = run([str(readelf), "--file-header", "--program-headers", "--dynamic", str(temporary_binary)])
            if not re.search(r"\(FLAGS_1\).*\bPIE\b", report):
                raise RuntimeError("ELF dynamic flags do not identify a PIE executable")
            dependencies = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", report)
            if not dependencies or set(dependencies) - {"libc.so", "libdl.so", "liblog.so", "libm.so"}:
                raise RuntimeError(f"Unexpected Android shared-library dependencies: {dependencies!r}")
            elf["needed"] = dependencies
            require_unchanged_sources(ROOT, snapshot)
            temporary_binary.replace(binary)
        finally:
            temporary_binary.unlink(missing_ok=True)
        (output / f"{abi}-elf.txt").write_text(report, encoding="utf-8")
        result = {"abi": abi, "api": args.api, "path": binary.relative_to(ROOT).as_posix(),
                  "bytes": binary.stat().st_size, "sha256": file_sha256(binary), "elf": elf,
                  "toolchain": toolchain, "build_config": config}
        results[abi] = result
        print(json.dumps(result), flush=True)
    if args.windows:
        binary = output / "pgwaa-windows-amd64.exe"
        print("Building Windows test worker ...", flush=True)
        run([args.go, "build", *GO_BUILD_FLAGS, "-ldflags=-s -w -buildid=",
             "-o", str(binary), "."], env=dict(env, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0"))
        probe = subprocess.run([str(binary)], input='{"id":1,"op":"ping"}\n', capture_output=True,
                               text=True, timeout=15, check=True)
        ping = json.loads(probe.stdout)
        if ping != {"id": 1, "result": {"runtime": "goja-firefox", "goos": "windows", "goarch": "amd64"}}:
            raise RuntimeError("Windows worker ping returned unexpected data")
        results["windows-amd64"] = {"abi": "windows-amd64", "path": binary.relative_to(ROOT).as_posix(),
                                    "bytes": binary.stat().st_size, "sha256": file_sha256(binary), "ping": ping,
                                    "toolchain": toolchain, "build_config": {
                                        "go_flags": GO_BUILD_FLAGS, "ldflags": "-s -w -buildid=",
                                        "environment": dict(COMMON_BUILD_ENV, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0")}}
    write_cache_manifest(ROOT, snapshot, results, toolchain)
    print(f"Manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr)
        raise SystemExit(exc.returncode)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Native build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
