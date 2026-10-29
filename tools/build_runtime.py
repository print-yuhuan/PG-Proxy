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


ROOT = Path(__file__).resolve().parents[1]
ABIS = {
    "arm64-v8a": ("arm64", "aarch64-linux-android", 183),
    "x86_64": ("amd64", "x86_64-linux-android", 62),
}


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
    parser.add_argument("--api", type=int, default=28)
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
    env = dict(os.environ, GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN="local", GOFLAGS="", GOWORK="off", GOENV="off")
    go_version = run([args.go, "version"], env=env)
    ndk_version = next(
        (line.split("=", 1)[1].strip() for line in (Path(args.ndk) / "source.properties").read_text().splitlines()
         if line.startswith("Pkg.Revision")), "unknown"
    )
    results = []
    for abi in args.abi or list(ABIS):
        arch, target, machine = ABIS[abi]
        binary = ROOT / "app/src/main/jniLibs" / abi / "libpgwaa.so"
        binary.parent.mkdir(parents=True, exist_ok=True)
        build_env = dict(env, GOOS="android", GOARCH=arch, CGO_ENABLED="1",
                         CC=f'"{clang}" --target={target}{args.api}')
        flags = "-s -w -buildid= -linkmode=external -extldflags=-Wl,-z,max-page-size=16384,-z,common-page-size=16384"
        print(f"Building Android {abi}, API {args.api} ...", flush=True)
        run([args.go, "build", "-mod=vendor", "-trimpath", "-buildvcs=false", "-buildmode=pie",
             "-ldflags", flags, "-o", str(binary), "."], env=build_env)
        elf = elf_metadata(binary, machine)
        report = run([str(readelf), "--file-header", "--program-headers", "--dynamic", str(binary)])
        if not re.search(r"\(FLAGS_1\).*\bPIE\b", report):
            raise RuntimeError("ELF dynamic flags do not identify a PIE executable")
        dependencies = re.findall(r"\(NEEDED\).*\[([^\]]+)\]", report)
        if not dependencies or set(dependencies) - {"libc.so", "libdl.so", "liblog.so", "libm.so"}:
            raise RuntimeError(f"Unexpected Android shared-library dependencies: {dependencies!r}")
        elf["needed"] = dependencies
        (output / f"{abi}-elf.txt").write_text(report, encoding="utf-8")
        result = {"abi": abi, "api": args.api, "path": str(binary.relative_to(ROOT)).replace("\\", "/"),
                  "bytes": binary.stat().st_size, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "elf": elf}
        results.append(result)
        print(json.dumps(result), flush=True)
    if args.windows:
        binary = output / "pgwaa-windows-amd64.exe"
        print("Building Windows test worker ...", flush=True)
        run([args.go, "build", "-mod=vendor", "-trimpath", "-buildvcs=false", "-ldflags=-s -w -buildid=",
             "-o", str(binary), "."], env=dict(env, GOOS="windows", GOARCH="amd64", CGO_ENABLED="0"))
        probe = subprocess.run([str(binary)], input='{"id":1,"op":"ping"}\n', capture_output=True,
                               text=True, timeout=15, check=True)
        ping = json.loads(probe.stdout)
        if ping != {"id": 1, "result": {"runtime": "goja-firefox", "goos": "windows", "goarch": "amd64"}}:
            raise RuntimeError("Windows worker ping returned unexpected data")
        results.append({"abi": "windows-amd64", "path": str(binary.relative_to(ROOT)).replace("\\", "/"),
                        "bytes": binary.stat().st_size, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "ping": ping})
    manifest = {"go": go_version, "ndk": ndk_version, "offline_modules": True,
                "android_app_execution_verified": False, "artifacts": results}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Manifest: {output / 'manifest.json'}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(exc.stderr)
        raise SystemExit(exc.returncode)
