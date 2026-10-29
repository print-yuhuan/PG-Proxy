"""Collect locally available third-party notices for inclusion in PG Proxy APKs.

No network access is performed. Generated notices are committed with the source
so a clean checkout does not require Rust caches to preserve attribution. This
script never clears the destination, and retains existing groups when their
development toolchains/caches are unavailable. Chaquopy and CPython notices are
maintained separately in the same asset directory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "app/src/main/assets/third_party"
PREFIXES = ("LICENSE", "LICENCE", "NOTICE", "COPYING", "COPYRIGHT", "UNLICENSE")
REQUIRED = (
    "project/LICENSE.txt", "project/NOTICE.txt", "project/AIStudio2API-LICENSE.txt",
    "go/Go-LICENSE.txt", "go/Go-PATENTS.txt", "go/vendor-notices.txt",
    "rust/crate-notices.txt", "rust/stdlib/COPYRIGHT-library.html",
    "rust/stdlib/licenses/MIT.txt", "rust/stdlib/licenses/Apache-2.0.txt",
    "Chaquopy-LICENSE.txt", "CPython-LICENSE.txt",
    "gecko/NOTICE.txt", "gecko/Mozilla-license.html", "gecko/source.json",
    "gecko/pdfjs/LICENSE", "gecko/pdfjs/LICENSE_FOXIT", "gecko/pdfjs/LICENSE_LIBERATION",
)
GECKO_VERSION = "159.0.20261022150525"
GECKO_REVISION = "5add844c26613d7e4d84c799d6e23cf987b1240d"
GECKO_AAR_SHA256 = "11f0dd1b030370e29e47030d1269943361f751c5220aff24dca3232e36646c90"


def collect_gecko(explicit):
    """Copy the exact engine's own notices from its locally cached AAR."""
    gradle = Path(os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle")))
    candidates = ([explicit] if explicit else sorted((gradle / "caches/modules-2/files-2.1/org.mozilla.geckoview/geckoview"
                                                     / GECKO_VERSION).glob("*/*.aar")))
    aar = next((p for p in candidates if p.is_file()), None)
    if explicit and not aar:
        raise RuntimeError("Explicit GeckoView AAR was not found")
    if not aar:
        # Clean source checkouts contain these notices already. Do not replace
        # them with another engine version or require a network connection.
        return
    with aar.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != GECKO_AAR_SHA256:
        raise RuntimeError("GeckoView AAR does not match the pinned license source build")
    folder = DESTINATION / "gecko"
    folder.mkdir(parents=True, exist_ok=True)
    source = "https://hg.mozilla.org/releases/mozilla-release/rev/" + GECKO_REVISION
    entries = {
        "chrome/toolkit/content/global/license.html": "Mozilla-license.html",
        "chrome/pdfjs/content/web/cmaps/LICENSE": "pdfjs/LICENSE",
        "chrome/pdfjs/content/web/standard_fonts/LICENSE_FOXIT": "pdfjs/LICENSE_FOXIT",
        "chrome/pdfjs/content/web/standard_fonts/LICENSE_LIBERATION": "pdfjs/LICENSE_LIBERATION",
    }
    with zipfile.ZipFile(aar) as archive:
        omni_bytes = archive.read("assets/omni.ja")
        with zipfile.ZipFile(io.BytesIO(omni_bytes)) as omni:
            for original, destination in entries.items():
                text = omni.read(original).decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                target = folder / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
    notice = ("PG Proxy — Mozilla GeckoView browser engine\n\n"
              "This application redistributes the unmodified Mozilla GeckoView engine:\n"
              "org.mozilla.geckoview:geckoview:" + GECKO_VERSION + "\n\n"
              "Mozilla Public License 2.0 and the upstream third-party licenses apply.\n"
              "MPL 2.0: https://www.mozilla.org/MPL/2.0/\n"
              "Matching source code for this exact engine build:\n" + source + "\n\n"
              "Mozilla-license.html is copied from this engine's bundled\n"
              "chrome/toolkit/content/global/license.html in assets/omni.ja. It contains\n"
              "the full MPL 2.0 text and upstream third-party notices. The pdfjs directory\n"
              "retains the additional bundled character-map and font license files.\n"
              "The engine also exposes its notices at about:license.\n\n"
              "PG Proxy's Kotlin integration and session-bridge extension are separate\n"
              "application code; they do not modify GeckoView or Mozilla's source files.\n"
              "Mozilla and Firefox trademarks belong to their respective owners.\n"
              "PG Proxy is not an official Mozilla product.\n")
    (folder / "NOTICE.txt").write_text(notice, encoding="utf-8", newline="\n")
    metadata = {"coordinate": "org.mozilla.geckoview:geckoview:" + GECKO_VERSION,
                "revision": GECKO_REVISION, "source": source, "aar_sha256": checksum,
                "omni_sha256": digest(omni_bytes),
                "notice_sources": entries, "engine_modified": False}
    (folder / "source.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8", newline="\n")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def license_files(directory: Path):
    return sorted((p for p in directory.rglob("*") if p.is_file() and p.name.upper().startswith(PREFIXES)),
                  key=lambda p: p.relative_to(directory).as_posix())


def copy_file(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    # All committed notices use UTF-8 and LF, including those copied from SDKs.
    destination.write_bytes(text_file(source).encode("utf-8"))


def text_file(path: Path) -> str:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def section(label: str, path: Path) -> str:
    return ("\n" + "=" * 78 + "\nSOURCE: " + label + "\nSHA-256: " + digest(path.read_bytes())
            + "\n" + "=" * 78 + "\n\n" + text_file(path).rstrip() + "\n")


def local_go_root(explicit):
    candidates = [explicit, os.getenv("GOROOT")]
    executable = shutil.which("go")
    if executable:
        result = subprocess.run([executable, "env", "GOROOT"], capture_output=True, text=True,
                                env=dict(os.environ, GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off"))
        if result.returncode == 0:
            candidates.append(result.stdout.strip())
    for value in candidates:
        if value and (Path(value) / "LICENSE").is_file():
            return Path(value).resolve()
    return None


def check_required():
    missing = [name for name in REQUIRED if not (DESTINATION / name).is_file() or (DESTINATION / name).stat().st_size < 100]
    if missing:
        raise RuntimeError("Required bundled notices are missing or empty: " + ", ".join(missing))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go-root", type=Path, help="Go SDK directory; otherwise GOROOT or local go env GOROOT")
    parser.add_argument("--cargo-home", type=Path, default=os.environ.get("CARGO_HOME"), help="Cargo cache; defaults to project data/python-wheels/cargo")
    parser.add_argument("--rustup-home", type=Path, default=os.environ.get("RUSTUP_HOME"), help="Rustup installation; defaults to project data/python-wheels/rustup")
    parser.add_argument("--rust-toolchain", default="1.99.1", help="Rust toolchain version containing standard-library notices")
    parser.add_argument("--pydantic-source", type=Path, help="Extracted pydantic-core source containing Cargo.lock")
    parser.add_argument("--gecko-aar", type=Path, help="Pinned GeckoView AAR; otherwise use its local Gradle cache or retain committed notices")
    parser.add_argument("--check", action="store_true", help="Read-only check that committed runtime notices are present")
    args = parser.parse_args()
    if args.check:
        check_required()
        print("Bundled third-party notices are present")
        return
    DESTINATION.mkdir(parents=True, exist_ok=True)
    manifest_path = DESTINATION / "collection.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"schema": 1}
    collect_gecko(args.gecko_aar)

    for source, destination in (("LICENSE", "project/LICENSE.txt"), ("NOTICE", "project/NOTICE.txt"),
                                ("THIRD_PARTY_LICENSE_AIStudio2API.txt", "project/AIStudio2API-LICENSE.txt")):
        copy_file(ROOT / source, DESTINATION / destination)

    go_root = local_go_root(args.go_root)
    if go_root:
        for name in ("LICENSE", "PATENTS"):
            copy_file(go_root / name, DESTINATION / "go" / ("Go-" + name + ".txt"))
        version = go_root / "VERSION"
        manifest["go_sdk"] = version.read_text().splitlines()[0] if version.is_file() else "local SDK"
    elif not (DESTINATION / "go/Go-LICENSE.txt").is_file():
        raise RuntimeError("Supply --go-root once to collect the Go SDK license")
    vendor = ROOT / "runtime/vendor"
    vendor_files = license_files(vendor)
    if not vendor_files:
        raise RuntimeError("Vendored Go dependency license files are missing")
    content = "PG Proxy — bundled Go dependency notices\n\nPaths below preserve source attribution within runtime/vendor.\n"
    for source in vendor_files:
        content += section("runtime/vendor/" + source.relative_to(vendor).as_posix(), source)
    target = DESTINATION / "go/vendor-notices.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    manifest["go_vendor_license_files"] = len(vendor_files)

    cargo_home = (args.cargo_home or ROOT / "data/python-wheels/cargo").resolve()
    source = args.pydantic_source or ROOT / "data/python-wheels/pydantic_core-2.50.1"
    lock = source / "Cargo.lock"
    if lock.is_file() and any((cargo_home / "registry/src").glob("*/*/Cargo.toml")):
        registry_roots = sorted((cargo_home / "registry/src").glob("*"))
        packages = tomllib.loads(lock.read_text(encoding="utf-8"))["package"]
        copied = []
        unavailable = []
        without_license = []
        content = ("PG Proxy — pydantic-core and downloaded Cargo dependency notices\n\n"
                   "Entries are identified by Cargo.lock package name and version. This is a\n"
                   "conservative collection of available source dependencies, including build-only\n"
                   "dependencies. A package being listed does not imply it is linked into every ABI.\n"
                   "All text is collected from the local pinned source/cache; no license is inferred\n"
                   "or substituted. Standard-library notices are bundled separately.\n")
        for package in sorted(packages, key=lambda p: (p["name"], p["version"])):
            identifier = package["name"] + "-" + package["version"]
            if package.get("source", "").startswith("registry+"):
                directory = next((p / identifier for p in registry_roots if (p / identifier).is_dir()), None)
            elif not package.get("source") and package["name"] == "pydantic-core":
                directory = source
            else:
                directory = None
            if directory is None:
                unavailable.append(identifier)
                continue
            # Do not descend into the root project's generated Cargo target tree.
            files = [p for p in license_files(directory) if "target" not in p.relative_to(directory).parts
                     and ".git" not in p.relative_to(directory).parts]
            metadata = tomllib.loads((directory / "Cargo.toml").read_text(encoding="utf-8")).get("package", {})
            declared = metadata.get("license", "See source license-file")
            content += "\n\nPACKAGE: " + identifier + "\nDECLARED LICENSE: " + str(declared) + "\n"
            if metadata.get("repository"):
                content += "UPSTREAM: " + str(metadata["repository"]) + "\n"
            if package.get("checksum"):
                content += "CRATE ARCHIVE SHA-256: " + package["checksum"] + "\n"
            for path in files:
                content += section(identifier + "/" + path.relative_to(directory).as_posix(), path)
            if not files:
                without_license.append(identifier)
                content += "No standalone license file found in this locally downloaded source.\n"
            copied.append(identifier)
        if without_license:
            raise RuntimeError("Downloaded dependencies lack license files; review before packaging: " + ", ".join(without_license))
        target = DESTINATION / "rust/crate-notices.txt"
        previous = manifest.get("cargo", {})
        partial_cache = (target.is_file() and previous.get("lock_sha256") == digest(lock.read_bytes())
                         and not set(previous.get("packages_collected", [])).issubset(copied))
        if partial_cache:
            print("Retaining committed crate notices: the local Cargo cache is incomplete")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            manifest["cargo"] = {"lock_sha256": digest(lock.read_bytes()), "packages_collected": copied,
                                 "lock_packages_not_in_local_cache": unavailable}
    elif not (DESTINATION / "rust/crate-notices.txt").is_file():
        raise RuntimeError("Build Python wheels once or provide --cargo-home/--pydantic-source to collect crate notices")

    rustup = (args.rustup_home or ROOT / "data/python-wheels/rustup").resolve()
    candidates = sorted((rustup / "toolchains").glob(args.rust_toolchain + "*"))
    doc = next((p / "share/doc/rust" for p in candidates if (p / "share/doc/rust/COPYRIGHT-library.html").is_file()), None)
    if doc:
        copy_file(doc / "COPYRIGHT-library.html", DESTINATION / "rust/stdlib/COPYRIGHT-library.html")
        for path in sorted((doc / "licenses").rglob("*")):
            if path.is_file():
                copy_file(path, DESTINATION / "rust/stdlib/licenses" / path.relative_to(doc / "licenses"))
        manifest["rust_stdlib_toolchain"] = args.rust_toolchain
    elif not (DESTINATION / "rust/stdlib/COPYRIGHT-library.html").is_file():
        raise RuntimeError("Rust standard-library notices are unavailable; supply --rustup-home")
    # Normalize retained groups and separately maintained CPython/Chaquopy text
    # as well. Hash only the normalized bytes which Git and APK builds consume.
    for path in DESTINATION.rglob("*"):
        if path.is_file() and path != manifest_path:
            path.write_bytes(text_file(path).encode("utf-8"))
    check_required()
    manifest["assets"] = [{"path": path.relative_to(DESTINATION).as_posix(), "bytes": path.stat().st_size,
                           "sha256": digest(path.read_bytes())}
                          for path in sorted(DESTINATION.rglob("*")) if path.is_file() and path != manifest_path]
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"go_vendor_license_files": len(vendor_files),
                      "cargo_packages_collected": len(manifest.get("cargo", {}).get("packages_collected", [])),
                      "cargo_lock_packages_not_in_cache": len(manifest.get("cargo", {}).get("lock_packages_not_in_local_cache", [])),
                      "bundled_files": len(manifest["assets"]), "destination": str(DESTINATION)}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("License collection failed: " + str(error))
        raise SystemExit(1)
