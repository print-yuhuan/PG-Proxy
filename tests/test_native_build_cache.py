"""Native provenance tests use local synthetic inputs, never SDK downloads or devices."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture
def native(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    return importlib.import_module("build_runtime")


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "repo"
    for name in ("runtime/go.mod", "runtime/main.go", "runtime/waa/dom.js",
                 "runtime/vendor/example/package.go", "runtime/waa/intl/data.json.gz",
                 "tools/build_runtime.py", "tools/build_app.py"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic " + name).encode())
    (root / "artifacts/runtime").mkdir(parents=True)
    return root


def toolchain():
    return {"go": "go version go1.27.3 windows/amd64", "ndk": "28.2.13676358", "clang": "Android clang version 19.0.0"}


def write_artifact(native, root, abi):
    relative = f"app/src/main/jniLibs/{abi}/libpgwaa.so"
    binary = root / relative
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(("synthetic worker " + abi).encode())
    return {"abi": abi, "api": 28, "path": relative, "bytes": binary.stat().st_size,
            "sha256": native.file_sha256(binary), "toolchain": toolchain(),
            "build_config": native.android_build_config(abi)}


def make_cache(native, root, abis=("arm64-v8a",)):
    snapshot = native.source_snapshot(root)
    records = {abi: write_artifact(native, root, abi) for abi in abis}
    native.write_cache_manifest(root, snapshot, records, toolchain())
    return records


@pytest.mark.parametrize("relative", [
    "runtime/new_untracked.go", "runtime/go.mod", "runtime/waa/dom.js",
    "runtime/vendor/example/package.go", "runtime/waa/intl/data.json.gz",
    "tools/build_runtime.py", "tools/build_app.py",
])
def test_source_binding_includes_untracked_vendor_embeds_and_build_scripts(native, source_tree, relative):
    make_cache(native, source_tree)
    before = native.verify_native_cache(source_tree, ["arm64-v8a"])
    path = source_tree / relative
    path.write_bytes(b"new or changed build input")
    assert native.source_snapshot(source_tree)["sha256"] != before["source_sha256"]
    with pytest.raises(RuntimeError, match="source digest differs"):
        native.verify_native_cache(source_tree, ["arm64-v8a"])
    assert native.reusable_artifacts(source_tree, native.source_snapshot(source_tree)) == {}


def test_removed_input_invalidates_cache_but_unrelated_app_edits_do_not(native, source_tree):
    make_cache(native, source_tree)
    expected = native.verify_native_cache(source_tree, ["arm64-v8a"])
    (source_tree / "README.md").write_text("changed app documentation")
    assert native.verify_native_cache(source_tree, ["arm64-v8a"]) == expected
    (source_tree / "runtime/waa/dom.js").unlink()
    with pytest.raises(RuntimeError, match="source digest differs"):
        native.verify_native_cache(source_tree, ["arm64-v8a"])


@pytest.mark.parametrize("contents", [None, "not json", "[]", '{"artifacts": []}',
                                       '{"schema_version": 2, "source": [], "artifacts": []}'])
def test_missing_or_legacy_manifest_cannot_be_reused(native, source_tree, contents):
    if contents is not None:
        (source_tree / "artifacts/runtime/manifest.json").write_text(contents)
    with pytest.raises(RuntimeError, match="manifest|source provenance"):
        native.verify_native_cache(source_tree, ["arm64-v8a"])


@pytest.mark.parametrize("mutation", ["bytes", "hash", "missing", "path", "api", "config", "toolchain", "duplicate", "abi"])
def test_modified_worker_or_incomplete_provenance_is_rejected(native, source_tree, mutation):
    records = make_cache(native, source_tree)
    path = source_tree / "artifacts/runtime/manifest.json"
    manifest = json.loads(path.read_text())
    record = manifest["artifacts"][0]
    worker = source_tree / records["arm64-v8a"]["path"]
    if mutation == "bytes":
        worker.write_bytes(b"truncated")
    elif mutation == "hash":
        worker.write_bytes(b"X" * worker.stat().st_size)
    elif mutation == "missing":
        worker.unlink()
    elif mutation == "path":
        record["path"] = "../external-worker.so"
    elif mutation == "api":
        record["api"] = 35
    elif mutation == "config":
        record["build_config"]["ldflags"] = "-s"
    elif mutation == "toolchain":
        del record["toolchain"]["ndk"]
    elif mutation == "duplicate":
        manifest["artifacts"].append(copy.deepcopy(record))
    elif mutation == "abi":
        record["abi"] = "x86_64"
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        native.verify_native_cache(source_tree, ["arm64-v8a"])


def test_inputs_changed_during_hash_are_rejected(native, source_tree, monkeypatch):
    original = native.file_sha256
    changed = source_tree / "runtime/main.go"
    def hash_then_edit(path):
        digest = original(path)
        if path == changed:
            path.write_bytes(path.read_bytes() + b" edit during hash")
        return digest
    monkeypatch.setattr(native, "file_sha256", hash_then_edit)
    with pytest.raises(RuntimeError, match="changed while hashing"):
        native.source_snapshot(source_tree)


def test_changed_sources_cannot_replace_a_previous_manifest(native, source_tree):
    make_cache(native, source_tree)
    path = source_tree / "artifacts/runtime/manifest.json"
    before = path.read_bytes()
    snapshot = native.source_snapshot(source_tree)
    (source_tree / "runtime/main.go").write_text("edit during compilation")
    with pytest.raises(RuntimeError, match="changed during the build"):
        native.write_cache_manifest(source_tree, snapshot, {}, toolchain())
    assert path.read_bytes() == before
    assert not path.with_suffix(".json.tmp").exists()


@pytest.fixture
def simulated_build(native, source_tree, monkeypatch, tmp_path):
    ndk = tmp_path / "ndk"
    host = {"Windows": "windows-x86_64", "Linux": "linux-x86_64", "Darwin": "darwin-x86_64"}[native.platform.system()]
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("clang", "llvm-readelf"):
        path = ndk / "toolchains/llvm/prebuilt" / host / "bin" / (name + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (ndk / "source.properties").write_text("Pkg.Revision = 28.2.13676358\n")
    calls = []
    after_compile = []
    def run(command, *, env=None):
        calls.append(command)
        if command[1:] == ["version"]:
            return toolchain()["go"]
        if command[1:] == ["--version"]:
            return toolchain()["clang"]
        if command[1] == "build":
            assert env["GOPROXY"] == "off" and env["GOWORK"] == "off"
            Path(command[command.index("-o") + 1]).write_bytes(("compiled " + env["GOARCH"]).encode())
            for callback in after_compile:
                callback()
            return ""
        assert "--file-header" in command
        return "(FLAGS_1) Flags: PIE\n(NEEDED) Shared library: [libc.so]\n"
    monkeypatch.setattr(native, "ROOT", source_tree)
    monkeypatch.setattr(native, "run", run)
    monkeypatch.setattr(native, "elf_metadata", lambda path, machine: {"machine": machine})
    def invoke(abi):
        monkeypatch.setattr(sys, "argv", ["build_runtime.py", "--go", "synthetic-go", "--ndk", str(ndk), "--abi", abi])
        native.main()
    return invoke, calls, after_compile


def test_separate_abi_builds_preserve_verified_incremental_cache(native, source_tree, simulated_build):
    invoke, _, _ = simulated_build
    invoke("arm64-v8a")
    arm = native.verify_native_cache(source_tree, ["arm64-v8a"])["artifacts"]["arm64-v8a"]
    invoke("x86_64")
    both = native.verify_native_cache(source_tree, ["arm64-v8a", "x86_64"])
    assert both["artifacts"]["arm64-v8a"] == arm
    assert both["artifacts"]["x86_64"]["build_config"]["compiler_target"] == "x86_64-linux-android28"
    invoke("arm64-v8a")
    assert native.verify_native_cache(source_tree, ["arm64-v8a", "x86_64"]) == both


def test_source_edit_during_compilation_keeps_previous_binary_and_manifest(native, source_tree, simulated_build):
    invoke, _, after_compile = simulated_build
    invoke("arm64-v8a")
    manifest = source_tree / "artifacts/runtime/manifest.json"
    binary = source_tree / "app/src/main/jniLibs/arm64-v8a/libpgwaa.so"
    before = (manifest.read_bytes(), binary.read_bytes())
    after_compile.append(lambda: (source_tree / "runtime/new.go").write_text("new input during compilation"))
    with pytest.raises(RuntimeError, match="changed during the build"):
        invoke("arm64-v8a")
    assert (manifest.read_bytes(), binary.read_bytes()) == before
    assert not binary.with_suffix(".so.building").exists()


def test_build_after_source_edit_drops_old_abi_until_it_is_rebuilt(native, source_tree, simulated_build):
    invoke, _, _ = simulated_build
    invoke("arm64-v8a")
    (source_tree / "runtime/new.go").write_text("new untracked input")
    invoke("x86_64")
    native.verify_native_cache(source_tree, ["x86_64"])
    with pytest.raises(RuntimeError, match="exactly one arm64-v8a"):
        native.verify_native_cache(source_tree, ["arm64-v8a"])
    invoke("arm64-v8a")
    native.verify_native_cache(source_tree, ["arm64-v8a", "x86_64"])


def test_package_only_rejects_apk_with_a_stale_worker(native, source_tree, monkeypatch):
    make_cache(native, source_tree)
    builder = importlib.import_module("build_app")
    monkeypatch.setattr(builder, "ROOT", source_tree)
    monkeypatch.setattr(builder, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "local_sdk", lambda: None)
    monkeypatch.setattr(builder, "local_ndk", lambda _sdk: None)
    monkeypatch.setattr(builder, "verify_apk", lambda *_a: {"waa_worker_sha256": "0" * 64})
    directory = source_tree / "app/build/outputs/apk/arm64/debug"
    directory.mkdir(parents=True)
    (directory / "output-metadata.json").write_text(json.dumps({"applicationId": "dev.pgproxy.app", "elements": [
        {"versionName": "0.3.0", "versionCode": 30, "outputFile": "app.apk"}]}))
    monkeypatch.setattr(sys, "argv", ["build_app.py", "--abi", "arm64-v8a", "--package-only"])
    with pytest.raises(RuntimeError, match="stale WAA worker"):
        builder.main()
    assert not list((source_tree / "artifacts").glob("*.apk"))


def test_skip_native_build_rejects_missing_provenance_before_gradle(native, source_tree, monkeypatch):
    write_artifact(native, source_tree, "arm64-v8a")
    builder = importlib.import_module("build_app")
    monkeypatch.setattr(builder, "ROOT", source_tree)
    monkeypatch.setattr(builder, "local_sdk", lambda: source_tree / "sdk")
    monkeypatch.setattr(builder, "local_ndk", lambda _sdk: None)
    monkeypatch.setattr(builder, "executable", lambda *_a: source_tree / "java")
    calls = []
    monkeypatch.setattr(builder, "run", lambda command, **_k: calls.append(command))
    monkeypatch.setattr(sys, "argv", ["build_app.py", "--abi", "arm64-v8a", "--skip-native-build"])
    with pytest.raises(RuntimeError, match="manifest is missing"):
        builder.main()
    assert len(calls) == 1 and str(calls[0][1]).endswith("collect_licenses.py")
