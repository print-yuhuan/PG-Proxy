"""Build and verify PG Proxy APKs using the checked-in Gradle wrapper.

Go/NDK/Java paths come from flags, environment variables, or local tool discovery.
Rust is required only when explicitly rebuilding the Python native wheels.
No phone, Google account, credential export, or Termux installation is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from urllib.parse import urlsplit

from collect_licenses import REQUIRED as REQUIRED_LICENSES, GECKO_VERSION


ROOT = Path(__file__).resolve().parents[1]
FLAVORS = {"arm64-v8a": "arm64", "x86_64": "x64"}
GECKO_LIBRARIES = ("clearkey", "crashhelper", "crashtools", "freebl3", "gkcodecs", "lgpllibs",
                   "mozavcodec", "mozavutil", "mozglue", "nss3", "plugin-container", "softokn3", "xul")
SIGNING_VARIABLES = ("PG_SIGNING_STORE_FILE", "PG_SIGNING_STORE_PASSWORD",
                     "PG_SIGNING_KEY_ALIAS", "PG_SIGNING_KEY_PASSWORD")


def offline_wrapper_directory(properties_path, gradle_user):
    """Require the exact URL-specific distribution which Gradle Wrapper will use."""
    properties = {}
    for line in properties_path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith(("#", "!")):
            continue
        key, separator, value = line.partition("=")
        if separator:
            # Checked-in wrapper properties use ordinary Java-properties URL
            # escaping. Fail closed for unsupported continuation/unicode forms.
            value = re.sub(r"\\([:= /\\])", r"\1", value.strip())
            if "\\" in value:
                raise RuntimeError("Offline wrapper preflight does not support this properties escape")
            properties[key.strip()] = value
    url = properties.get("distributionUrl", "")
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password or not parsed.netloc:
        raise RuntimeError("Offline wrapper preflight requires the pinned HTTP(S) distribution URL")
    if properties.get("distributionBase", "GRADLE_USER_HOME") != "GRADLE_USER_HOME" or properties.get("zipStoreBase", "GRADLE_USER_HOME") != "GRADLE_USER_HOME":
        raise RuntimeError("Offline wrapper preflight requires GRADLE_USER_HOME distribution and ZIP storage")
    filename = parsed.path.rsplit("/", 1)[-1]
    match = re.fullmatch(r"(gradle-[0-9.]+)-(bin|all)\.zip", filename)
    if not match:
        raise RuntimeError("Unrecognized pinned Gradle distribution filename")
    # PathAssembler.getHash: positive MD5 BigInteger, represented in base 36.
    number = int.from_bytes(hashlib.md5(url.encode("utf-8"), usedforsecurity=False).digest(), "big")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while number:
        number, remainder = divmod(number, 36)
        encoded = alphabet[remainder] + encoded
    key = encoded or "0"
    relative = Path(filename[:-4]) / key
    directory = gradle_user / properties.get("distributionPath", "wrapper/dists") / relative / match[1]
    marker = gradle_user / properties.get("zipStorePath", "wrapper/dists") / relative / (filename + ".ok")
    roots = [entry for entry in directory.parent.iterdir() if entry.is_dir()] if directory.parent.is_dir() else []
    launchers = list((directory / "lib").glob("gradle-launcher-*.jar"))
    if not marker.is_file() or roots != [directory] or len(launchers) != 1:
        raise RuntimeError("Offline build requires this exact pinned wrapper URL to be installed first: " + str(directory))
    return directory


def available_file(candidates):
    return next((Path(value).expanduser().resolve() for value in candidates if value and Path(value).expanduser().is_file()), None)


def available_dir(candidates):
    return next((Path(value).expanduser().resolve() for value in candidates if value and Path(value).expanduser().is_dir()), None)


def local_sdk():
    explicit = available_dir([os.getenv("ANDROID_HOME"), os.getenv("ANDROID_SDK_ROOT")])
    if explicit:
        return explicit
    settings = ROOT / "local.properties"
    if settings.is_file():
        for line in settings.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("sdk.dir="):
                value = line.split("=", 1)[1].replace("\\:", ":").replace("\\\\", "\\")
                if Path(value).is_dir():
                    return Path(value).resolve()
    candidates = [Path.home() / "Android/Sdk", Path.home() / "Library/Android/sdk"]
    if os.getenv("LOCALAPPDATA"):
        candidates.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk")
    return available_dir(candidates)


def revision_key(path):
    return tuple(int(part) for part in re.findall(r"\d+", path.name))


def local_ndk(sdk):
    explicit = available_dir([os.getenv("ANDROID_NDK_HOME"), os.getenv("ANDROID_NDK_ROOT")])
    if explicit:
        return explicit
    candidates = sorted((sdk / "ndk").glob("*"), key=revision_key, reverse=True) if sdk else []
    return available_dir(candidates)


def executable(name, home_variable=None):
    suffix = ".exe" if os.name == "nt" else ""
    candidates = []
    if home_variable and os.getenv(home_variable):
        candidates.append(Path(os.environ[home_variable]) / "bin" / (name + suffix))
    candidates.append(shutil.which(name))
    return available_file(candidates)


def run(command, env=None):
    subprocess.run([str(item) for item in command], cwd=ROOT, env=env, check=True)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def release_signing_environment(env, root=ROOT):
    """Validate configuration without exposing or copying signing secrets."""
    missing = [name for name in SIGNING_VARIABLES if not env.get(name)]
    if missing:
        raise RuntimeError("Release signing requires environment variables: " + ", ".join(missing))
    store = Path(env["PG_SIGNING_STORE_FILE"]).expanduser()
    if not store.is_absolute() or not store.is_file():
        raise RuntimeError("Release keystore must be an existing absolute path outside the repository or in ignored signing/")
    checkout = root.resolve()
    for candidate in {store.absolute(), store.resolve()}:
        if not candidate.is_relative_to(checkout):
            continue
        relative = candidate.relative_to(checkout)
        if not relative.parts or relative.parts[0] != "signing":
            raise RuntimeError("Release keystore must be outside the repository or in ignored signing/")
        try:
            ignored = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative.as_posix()],
                                     cwd=checkout, capture_output=True)
            tracked = subprocess.run(["git", "--literal-pathspecs", "ls-files", "--error-unmatch", "--", relative.as_posix()],
                                     cwd=checkout, capture_output=True)
        except OSError:
            raise RuntimeError("Cannot verify that the local signing keystore is ignored and untracked") from None
        if ignored.returncode != 0 or tracked.returncode != 1:
            raise RuntimeError("Repository signing keystore must be Git-ignored and untracked")
    return store.resolve()


def release_verification_tools(sdk, java):
    """Use SDK tools directly, avoiding platform batch wrappers and shell quoting."""
    if not sdk or not java:
        raise RuntimeError("Release verification requires Android SDK and JDK; supply --sdk/--java")
    suffix = ".exe" if os.name == "nt" else ""
    keytool = java.parent / ("keytool" + suffix)
    for directory in sorted((sdk / "build-tools").glob("*"), key=revision_key, reverse=True):
        aapt = directory / ("aapt2" + suffix)
        signer = directory / "lib/apksigner.jar"
        if aapt.is_file() and signer.is_file() and keytool.is_file():
            return keytool, aapt, signer
    raise RuntimeError("Release verification requires JDK keytool and SDK aapt2/apksigner")


def capture_release_tool(command, env, label, *, binary=False):
    # A tool's diagnostic may contain local paths or key details. Show a bounded
    # operation label on failure, never relay subprocess output indiscriminately.
    result = subprocess.run([str(item) for item in command], cwd=ROOT, env=env,
                            capture_output=True, text=not binary)
    if result.returncode:
        raise RuntimeError(label + " failed; verify the local signing configuration and SDK tools")
    return result.stdout


def configured_certificate_sha256(store, keytool, env):
    certificate = capture_release_tool(
        [keytool, "-exportcert", "-keystore", store, "-alias", env["PG_SIGNING_KEY_ALIAS"],
         "-storepass:env", "PG_SIGNING_STORE_PASSWORD"], env, "Release certificate export", binary=True)
    if not certificate:
        raise RuntimeError("Release certificate export returned no certificate")
    return hashlib.sha256(certificate).hexdigest()


def validate_release_signature(output, expected_sha256):
    # Build Tools 37 labels v3 certificates "V3.0 Signer:" instead of
    # "Signer #1". The new label carries no index, so require its explicit
    # signer-count declaration; do not infer one signer from one printed hash.
    label = r"(Signer #\d+|V[1-3](?:\.\d+)? Signer):?"
    digests = re.findall(r"^" + label + r" certificate SHA-256 digest: ([0-9a-fA-F]{64})\s*$",
                         output, re.MULTILINE)
    counts = re.findall(r"^Number of signers: (\d+)\s*$", output, re.MULTILINE)
    if counts and counts != ["1"]:
        raise RuntimeError("Release APK must have exactly one signer")
    if len(digests) != 1 or digests[0][1].lower() != expected_sha256.lower():
        raise RuntimeError("Release APK signer does not match the configured release certificate")
    signer_label, certificate = digests[0]
    if ((signer_label.startswith("V") and counts != ["1"])
            or (signer_label.startswith("Signer #") and signer_label != "Signer #1")):
        raise RuntimeError("Release APK must have exactly one identified signer")
    names = re.findall(r"^" + label + r" certificate DN: (.+)$", output, re.MULTILINE)
    if (len(names) != 1 or names[0][0] != signer_label
            or re.search(r"\bCN\s*=\s*Android Debug\b", names[0][1], re.IGNORECASE)):
        raise RuntimeError("Release APK must use a non-debug signing identity")
    modern = re.search(r"^Verified using v[23](?:\.\d+)? scheme.*: true\s*$", output, re.MULTILINE)
    if not modern:
        raise RuntimeError("Release APK must have a verified v2 or v3 signature")
    return {"certificate_sha256": certificate.lower(), "signature_verified": True,
            "non_debug_certificate": True}


def validate_release_manifest(output, version, version_code):
    package_line = next((line for line in output.splitlines() if line.startswith("package:")), "")
    values = dict(re.findall(r"(\w+)='([^']*)'", package_line))
    if (values.get("name") != "dev.pgproxy.app" or values.get("versionName") != version
            or values.get("versionCode") != str(version_code)):
        raise RuntimeError("Release APK manifest identity/version does not match the build metadata")
    if re.search(r"^application-debuggable(?:\s|$)", output, re.MULTILINE):
        raise RuntimeError("Release APK is debuggable")
    if not re.search(r"^application(?:-label)?:", output, re.MULTILINE):
        raise RuntimeError("Release APK manifest has no application declaration")
    return {"manifest_identity_verified": True, "debuggable": False}


def verify_release_apk(path, java, aapt, signer, expected_sha256, version, version_code, env):
    signed = capture_release_tool([java, "-jar", signer, "verify", "--verbose", "--print-certs", path],
                                  env, "Release APK signature verification")
    identity = validate_release_signature(signed, expected_sha256)
    manifest = capture_release_tool([aapt, "dump", "badging", path], env, "Release APK manifest verification")
    identity.update(validate_release_manifest(manifest, version, version_code))
    return identity


def dex_login_inventory(data):
    """Read type definitions and app-owned string literals from standard DEX.

    UA checks use actual const-string instructions and static encoded values in
    PG Proxy classes. A shared DEX string table may also contain Mozilla's own
    UA compatibility strings; those third-party literals must not be rejected.
    """
    if len(data) < 112 or not re.fullmatch(rb"dex\n0(?:35|37|38|39|40)\x00", data[:8]):
        raise RuntimeError("APK contains an unsupported or malformed DEX file")

    def uint(offset, size=4):
        if offset < 0 or offset + size > len(data):
            raise RuntimeError("DEX structure is truncated")
        return int.from_bytes(data[offset:offset + size], "little")

    if uint(32) != len(data) or uint(40) != 0x12345678:
        raise RuntimeError("DEX size or byte order is invalid")

    def uleb(offset):
        result = 0
        for shift in range(0, 35, 7):
            byte = uint(offset, 1)
            offset += 1
            result |= (byte & 127) << shift
            if byte < 128:
                return result, offset
        raise RuntimeError("Invalid DEX variable-length integer")

    def table(count_offset, start_offset, width):
        count, start = uint(count_offset), uint(start_offset)
        if start + count * width > len(data):
            raise RuntimeError("DEX table exceeds file bounds")
        return range(start, start + count * width, width)

    strings = []
    for offset in table(56, 60, 4):
        _, start = uleb(uint(offset))
        end = data.find(b"\x00", start)
        if end < 0:
            raise RuntimeError("DEX string is unterminated")
        strings.append(data[start:end])

    def string(index):
        if index >= len(strings):
            raise RuntimeError("DEX string index is invalid")
        return strings[index]

    types = [string(uint(offset)) for offset in table(64, 68, 4)]
    app_literals = set()
    defined = set()

    def encoded_value(offset, depth=0):
        if depth > 64:
            raise RuntimeError("DEX encoded value nesting is excessive")
        header = uint(offset, 1)
        offset += 1
        kind, extra = header & 31, header >> 5
        if kind == 0x1C:  # VALUE_ARRAY
            count, offset = uleb(offset)
            for _ in range(count):
                offset = encoded_value(offset, depth + 1)
        elif kind == 0x1D:  # VALUE_ANNOTATION
            _, offset = uleb(offset)
            count, offset = uleb(offset)
            for _ in range(count):
                _, offset = uleb(offset)
                offset = encoded_value(offset, depth + 1)
        elif kind not in (0x1E, 0x1F):  # NULL and BOOLEAN have no following bytes.
            value = uint(offset, extra + 1)
            if kind == 0x17:  # VALUE_STRING
                app_literals.add(string(value))
            offset += extra + 1
        return offset

    # Instruction widths in 16-bit code units (standard, non-optimized DEX).
    widths = [1] * 256
    for opcode in (0x02, 0x05, 0x08, 0x13, 0x15, 0x16, 0x19, 0x1A, 0x1C, 0x1F,
                   0x20, 0x22, 0x23, 0x29, *range(0x2D, 0x3E), *range(0x44, 0x6E),
                   *range(0x90, 0xB0), *range(0xD0, 0xE3), 0xFE, 0xFF):
        widths[opcode] = 2
    for opcode in (0x03, 0x06, 0x09, 0x14, 0x17, 0x1B, 0x24, 0x25, 0x26, 0x2A,
                   0x2B, 0x2C, *range(0x6E, 0x73), *range(0x74, 0x79), 0xFC, 0xFD):
        widths[opcode] = 3
    widths[0x18] = 5
    widths[0xFA] = widths[0xFB] = 4
    unused = {*range(0x3E, 0x44), 0x73, 0x79, 0x7A, *range(0xE3, 0xFA)}

    def code_literals(offset):
        start, count = offset + 16, uint(offset + 12)
        if start + count * 2 > len(data):
            raise RuntimeError("DEX method code exceeds file bounds")
        cursor = 0
        while cursor < count:
            unit = uint(start + cursor * 2, 2)
            opcode = unit & 255
            width = widths[opcode]
            if opcode == 0 and unit:
                payload_count = uint(start + (cursor + 1) * 2, 2)
                if unit == 0x100:  # packed-switch payload
                    width = 4 + payload_count * 2
                elif unit == 0x200:  # sparse-switch payload
                    width = 2 + payload_count * 4
                elif unit == 0x300:  # fill-array-data payload
                    elements = uint(start + (cursor + 2) * 2)
                    width = 4 + (payload_count * elements + 1) // 2
                else:
                    raise RuntimeError("DEX method has an unknown payload")
            elif opcode in unused:
                raise RuntimeError("DEX method has an unsupported instruction")
            if cursor + width > count:
                raise RuntimeError("DEX instruction is truncated")
            if opcode in (0x1A, 0x1B):
                app_literals.add(string(uint(start + (cursor + 1) * 2, 2 if opcode == 0x1A else 4)))
            cursor += width

    for offset in table(96, 100, 32):
        index = uint(offset)
        if index >= len(types):
            raise RuntimeError("DEX class type index is invalid")
        descriptor = types[index]
        defined.add(descriptor)
        if not descriptor.startswith(b"Ldev/pgproxy/app/"):
            continue
        values = uint(offset + 28)
        if values:
            count, values = uleb(values)
            for _ in range(count):
                values = encoded_value(values)
        class_data = uint(offset + 24)
        if not class_data:
            continue
        counts = []
        for _ in range(4):
            count, class_data = uleb(class_data)
            counts.append(count)
        for _ in range(counts[0] + counts[1]):
            _, class_data = uleb(class_data)  # field index delta
            _, class_data = uleb(class_data)  # access flags
        for _ in range(counts[2] + counts[3]):
            _, class_data = uleb(class_data)  # method index delta
            _, class_data = uleb(class_data)  # access flags
            code, class_data = uleb(class_data)
            if code:
                code_literals(code)
    return set(types), defined, app_literals


def verify_gecko_only_login(apk):
    dex_names = sorted(name for name in apk.namelist() if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name))
    if not dex_names:
        raise RuntimeError("APK contains no DEX classes")
    types, defined, literals = set(), set(), set()
    for name in dex_names:
        dex_types, dex_defined, app_literals = dex_login_inventory(apk.read(name))
        types.update(dex_types)
        defined.update(dex_defined)
        literals.update(app_literals)
    old_classes = ("LoginActivity", "BrowserProfiles", "BrowerProfiles", "BrowserSessionCapture", "BrowserLoginDiagnostics")
    for name in old_classes:
        descriptor = ("Ldev/pgproxy/app/" + name).encode("ascii")
        if any(value == descriptor + b";" or value.startswith(descriptor + b"$") for value in types):
            raise RuntimeError("APK retains a removed application login class: " + name)
    if any(value.startswith(b"Landroidx/webkit/") for value in types):
        raise RuntimeError("APK retains the removed AndroidX WebKit dependency")
    if b"Ldev/pgproxy/app/GeckoLoginActivity;" not in defined:
        raise RuntimeError("APK is missing the Gecko login activity implementation")
    if any(re.search(rb"\bChrome/[0-9]", value) for value in literals):
        raise RuntimeError("APK retains a Chrome user-agent literal in application code")


def verify_apk(path, abi):
    """Reject runtime-only probe APKs and accidental credential-file packaging."""
    with zipfile.ZipFile(path) as apk:
        names = set(apk.namelist())
        verify_gecko_only_login(apk)
        expected = {f"lib/{abi}/libpgwaa.so", f"lib/{abi}/libpython3.14.so",
                    "assets/chaquopy/app.imy", "assets/chaquopy/requirements-common.imy",
                    f"assets/chaquopy/requirements-{abi}.imy", "assets/chaquopy/cacert.pem"}
        expected.update("assets/third_party/" + name for name in REQUIRED_LICENSES)
        expected.update(f"lib/{abi}/lib{name}.so" for name in GECKO_LIBRARIES)
        expected.update({"assets/omni.ja", "assets/gecko-bridge/manifest.json",
                         "assets/gecko-bridge/background.js", "assets/gecko-bridge/page-ready.js"})
        if expected - names:
            raise RuntimeError(f"APK lacks required runtime entries: {sorted(expected - names)}")
        if any(apk.getinfo("assets/third_party/" + name).file_size < 100 for name in REQUIRED_LICENSES):
            raise RuntimeError("APK contains an empty or incomplete required license notice")
        native_abis = {name.split("/")[1] for name in names if name.startswith("lib/") and name.endswith(".so")}
        if native_abis != {abi}:
            raise RuntimeError("APK ABI does not match its artifact name")
        source = json.loads(apk.read("assets/third_party/gecko/source.json"))
        if source.get("coordinate") != "org.mozilla.geckoview:geckoview:" + GECKO_VERSION:
            raise RuntimeError("APK GeckoView attribution does not match the pinned engine")
        extension = json.loads(apk.read("assets/gecko-bridge/manifest.json"))
        permissions = set(extension.get("permissions", []))
        if extension.get("browser_specific_settings", {}).get("gecko", {}).get("id") != "session-bridge@pgproxy.local" or not {"cookies", "webRequest", "nativeMessaging", "geckoViewAddons"}.issubset(permissions):
            raise RuntimeError("APK GeckoView session-bridge manifest is incomplete")
        scripts = extension.get("background", {}).get("scripts", [])
        scripts += [script for entry in extension.get("content_scripts", []) for script in entry.get("js", [])]
        if not {"background.js", "page-ready.js"}.issubset(scripts) or any("assets/gecko-bridge/" + script not in names or apk.getinfo("assets/gecko-bridge/" + script).file_size < 100 for script in scripts):
            raise RuntimeError("APK GeckoView session-bridge scripts are missing")
        omni_bytes = apk.read("assets/omni.ja")
        if hashlib.sha256(omni_bytes).hexdigest() != source.get("omni_sha256"):
            raise RuntimeError("APK GeckoView omni.ja does not match its pinned source attribution")
        with zipfile.ZipFile(io.BytesIO(omni_bytes)) as omni:
            if "chrome/toolkit/content/global/license.html" not in omni.namelist():
                raise RuntimeError("APK GeckoView omni.ja is incomplete")
        for library in GECKO_LIBRARIES:
            with apk.open(f"lib/{abi}/lib{library}.so") as stream:
                header = stream.read(65536)
            if header[:6] != b"\x7fELF\x02\x01":
                raise RuntimeError("GeckoView library is not a 64-bit little-endian ELF: " + library)
            info = struct.unpack_from("<HHIQQQIHHHHHH", header, 16)
            loads = [struct.unpack_from("<IIQQQQQQ", header, info[4] + i * info[8]) for i in range(info[9])]
            if not any(entry[0] == 1 for entry in loads) or any(entry[0] == 1 and entry[7] < 16384 for entry in loads):
                raise RuntimeError("GeckoView library lacks 16 KB segment alignment: " + library)
        contents = set()
        for archive in ("app.imy", "requirements-common.imy", f"requirements-{abi}.imy"):
            with zipfile.ZipFile(io.BytesIO(apk.read("assets/chaquopy/" + archive))) as nested:
                contents.update(nested.namelist())
        for package in ("pgproxy", "fastapi", "uvicorn", "httpx", "pydantic", "pydantic_core"):
            if not any(name.startswith(package + "/") for name in contents):
                raise RuntimeError(f"APK does not contain {package}; a probe APK cannot be published as the complete app")
        if not any(name.startswith("pydantic_core/") and name.endswith(".so") for name in contents):
            raise RuntimeError("APK is missing the Android pydantic-core native extension")
        prohibited = {".env", "session.json", "storage_state.json", "browser-request.txt", "browser-generate-request.txt",
                      "test-session.json", "test-connection.json", "private-connection.json"}
        if any(Path(name).name in prohibited for name in names | contents):
            raise RuntimeError("An unexpected credential/configuration file was packaged into the APK")
        worker_hash = hashlib.sha256(apk.read(f"lib/{abi}/libpgwaa.so")).hexdigest()
    return {"python_packages_present": True, "pydantic_native_present": True,
            "third_party_notices_present": True,
            "gecko_engine_present": True, "gecko_session_bridge_present": True, "gecko_native_16kb_aligned": True,
            "gecko_only_login": True,
            "known_credential_filenames_absent": True, "waa_worker_sha256": worker_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abi", choices=[*FLAVORS, "all"], default="all")
    parser.add_argument("--build-type", choices=("debug", "release"), default="debug",
                        help="Release requires external PG_SIGNING_* configuration and verified signing")
    parser.add_argument("--go", help="Go executable; otherwise PG_GO, GOROOT or PATH")
    parser.add_argument("--ndk", type=Path, help="Android NDK directory; otherwise Android NDK environment or local SDK")
    parser.add_argument("--sdk", type=Path, help="Android SDK directory; otherwise Android SDK environment/local.properties")
    parser.add_argument("--java", help="JDK java executable; otherwise JAVA_HOME or PATH")
    parser.add_argument("--skip-native-build", action="store_true", help="Use previously built WAA binaries (still require them)")
    parser.add_argument("--build-wheels", action="store_true", help="Run the Android Python-wheel builder before Gradle")
    parser.add_argument("--bootstrap-rust", action="store_true", help="Explicitly install the wheel builder's pinned local Rust toolchain")
    parser.add_argument("--cargo-home", type=Path, help="Optional Cargo home passed to the wheel builder")
    parser.add_argument("--rustup-home", type=Path, help="Optional Rustup home passed to the wheel builder")
    parser.add_argument("--tests", action="store_true", help="Also build instrumentation APKs; does not run them")
    parser.add_argument("--test-abi", action="append", choices=FLAVORS,
                        help="Repeat to select instrumentation ABIs; requires --tests, defaults to all selected app ABIs")
    parser.add_argument("--offline", action="store_true", help="Gradle offline mode; cannot bootstrap Rust or rebuild wheels")
    parser.add_argument("--package-only", action="store_true", help="Verify and copy existing complete APKs; run no builds")
    args = parser.parse_args()
    if args.build_type == "release" and (args.tests or args.test_abi):
        parser.error("Release artifacts cannot include instrumentation; build tests with --build-type debug")
    abis = list(FLAVORS) if args.abi == "all" else [args.abi]
    if args.test_abi and not args.tests:
        parser.error("--test-abi requires --tests")
    test_abis = set(args.test_abi or abis) if args.tests else set()
    if test_abis - set(abis):
        parser.error("Instrumentation ABIs must also be selected by --abi")
    sdk = args.sdk.resolve() if args.sdk else local_sdk()
    ndk = args.ndk.resolve() if args.ndk else local_ndk(sdk)
    java = available_file([args.java]) if args.java else executable("java", "JAVA_HOME")
    go = available_file([args.go, os.getenv("PG_GO")]) or executable("go", "GOROOT")
    release_tools = None
    expected_certificate = None
    if args.build_type == "release":
        signing_env = dict(os.environ)
        store = release_signing_environment(signing_env)
        release_tools = release_verification_tools(sdk, java)
        expected_certificate = configured_certificate_sha256(store, release_tools[0], signing_env)
    if args.bootstrap_rust and not args.build_wheels:
        parser.error("--bootstrap-rust requires --build-wheels")
    if args.offline and (args.build_wheels or args.bootstrap_rust):
        parser.error("Offline mode requires prebuilt Python wheels")
    # This offline collector retains committed notices when Rust/Go development
    # caches are absent, while refreshing the project's own attribution text.
    run([sys.executable, ROOT / "tools/collect_licenses.py"])
    if not args.package_only:
        if not sdk or not java:
            parser.error("Install JDK 17+ and Android SDK; supply --java/--sdk or their environment variables")
        if not args.skip_native_build:
            if not go or not ndk:
                parser.error("Supply --go/--ndk or set PG_GO/GOROOT and ANDROID_NDK_HOME")
            command = [sys.executable, ROOT / "tools/build_runtime.py", "--go", go, "--ndk", ndk]
            for abi in abis:
                command += ["--abi", abi]
            run(command)
        for abi in abis:
            if not (ROOT / "app/src/main/jniLibs" / abi / "libpgwaa.so").is_file():
                parser.error(f"Missing prebuilt WAA worker for {abi}")
        if args.build_wheels:
            if not ndk:
                parser.error("The Python native-wheel builder requires --ndk or ANDROID_NDK_HOME")
            for index, abi in enumerate(abis):
                command = [sys.executable, ROOT / "tools/build_python_wheels.py", "--abi", abi, "--ndk", ndk]
                if args.bootstrap_rust and index == 0:
                    command.append("--bootstrap-rust")
                for option, value in (("--cargo-home", args.cargo_home), ("--rustup-home", args.rustup_home)):
                    if value:
                        command += [option, value]
                run(command)
        for abi in abis:
            tag = abi.replace("-", "_")
            if not list((ROOT / "artifacts/wheels").glob(f"pydantic_core-*-cp314-cp314-android_28_{tag}.whl")):
                parser.error(f"Missing {abi} pydantic-core wheel. Run with --build-wheels after preparing Rust, or build it separately.")
        wrapper = ROOT / "gradle/wrapper/gradle-wrapper.jar"
        if not wrapper.is_file():
            parser.error("The checked-in Gradle wrapper JAR is missing")
        tasks = []
        for abi in abis:
            variant = FLAVORS[abi][0].upper() + FLAVORS[abi][1:] + args.build_type.capitalize()
            tasks.append(":app:assemble" + variant)
            if abi in test_abis:
                tasks.append(":app:assemble" + variant + "AndroidTest")
        command = [java, "-classpath", wrapper, "org.gradle.wrapper.GradleWrapperMain", *tasks,
                   "--console=plain", "--no-daemon"]
        if args.offline:
            # The wrapper runs before Gradle can interpret --offline. Prevent it
            # from downloading a distribution when no local installation exists.
            gradle_user = Path(os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle")))
            offline_wrapper_directory(ROOT / "gradle/wrapper/gradle-wrapper.properties", gradle_user)
            command.append("--offline")
        env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
        if ndk:
            env["ANDROID_NDK_HOME"] = str(ndk)
        if args.offline:
            # Chaquopy invokes pip as a separate process: Gradle's flag alone
            # does not disable its indexes. Missing cached outputs fail clearly.
            env["PIP_NO_INDEX"] = "1"
        run(command, env=env)
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    records = []
    copies = []
    # Validate all selected inputs before replacing any previously packaged APK.
    # This avoids a partially updated artifact set when a test ABI is missing.
    for abi in test_abis:
        flavor = FLAVORS[abi]
        test_source = ROOT / "app/build/outputs/apk/androidTest" / flavor / "debug" / f"app-{flavor}-debug-androidTest.apk"
        if not test_source.is_file():
            variant = flavor[0].upper() + flavor[1:] + "DebugAndroidTest"
            raise RuntimeError(f"Missing instrumentation APK for {abi}: build :app:assemble{variant}, "
                               "or restrict --tests with --test-abi to the ABIs actually built")
    for abi in abis:
        flavor = FLAVORS[abi]
        directory = ROOT / "app/build/outputs/apk" / flavor / args.build_type
        metadata = json.loads((directory / "output-metadata.json").read_text(encoding="utf-8"))
        if metadata.get("applicationId") != "dev.pgproxy.app" or len(metadata["elements"]) != 1:
            raise RuntimeError("Unexpected APK identity or split layout")
        element = metadata["elements"][0]
        version = element["versionName"]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.-]*)", version):
            raise RuntimeError("Invalid artifact version")
        source = directory / element["outputFile"]
        checks = verify_apk(source, abi)
        signing = None
        if args.build_type == "release":
            signing = verify_release_apk(source, java, release_tools[1], release_tools[2], expected_certificate,
                                         version, element["versionCode"], signing_env)
        suffix = "-release" if args.build_type == "release" else ""
        destination = output / f"PG-Proxy-{version}-{abi}{suffix}.apk"
        copies.append((source, destination))
        records.append({"name": destination.name, "abi": abi, "version": version, "version_code": element["versionCode"],
                        "bytes": source.stat().st_size, "sha256": sha256(source), "build_type": args.build_type,
                        "checks": checks, "device_execution_verified_by_this_command": False})
        if signing:
            records[-1]["signing"] = signing
        if abi in test_abis:
            test_source = ROOT / "app/build/outputs/apk/androidTest" / flavor / "debug" / f"app-{flavor}-debug-androidTest.apk"
            test_destination = output / f"PG-Proxy-{version}-{abi}-androidTest.apk"
            copies.append((test_source, test_destination))
            records.append({"name": test_destination.name, "abi": abi, "kind": "instrumentation",
                            "bytes": test_source.stat().st_size, "sha256": sha256(test_source)})
    for source, destination in copies:
        shutil.copy2(source, destination)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True)
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True)
    report = {"name": "PG Proxy", "application_id": "dev.pgproxy.app", "packaged_at_utc": datetime.now(timezone.utc).isoformat(),
              "build_invoked": not args.package_only,
              "workspace_revision_at_packaging": revision.stdout.strip() if revision.returncode == 0 else None,
              "workspace_dirty_at_packaging": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
              "signing": ("Verified release signing with a private local keystore" if args.build_type == "release"
                          else "Android debug signing; not a release distribution"), "artifacts": records}
    manifest = output / ("pg-proxy-release-apks.json" if args.build_type == "release" else "pg-proxy-apks.json")
    manifest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Artifact manifest: {manifest}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
