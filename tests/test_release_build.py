"""Release distribution safeguards; all keys and tool responses are synthetic."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    return importlib.import_module('build_app')


def signing_env(path):
    return {'PG_SIGNING_STORE_FILE': str(path), 'PG_SIGNING_STORE_PASSWORD': 'synthetic-store-secret',
            'PG_SIGNING_KEY_ALIAS': 'synthetic-release', 'PG_SIGNING_KEY_PASSWORD': 'synthetic-key-secret'}


def test_release_configuration_rejects_missing_secrets_without_echoing_values(builder, tmp_path):
    env = signing_env(tmp_path / 'key.jks')
    del env['PG_SIGNING_KEY_PASSWORD']
    with pytest.raises(RuntimeError, match='PG_SIGNING_KEY_PASSWORD') as error:
        builder.release_signing_environment(env, tmp_path / 'repo')
    assert 'synthetic-store-secret' not in str(error.value)


def test_release_keystore_rejects_arbitrary_checkout_paths_and_missing_files(builder, tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    internal = root / 'signing.jks'
    external = tmp_path / 'release.jks'
    internal.write_text('not a real key')
    external.write_text('not a real key')
    for candidate in (internal, tmp_path / 'missing.jks', Path('relative.jks')):
        with pytest.raises(RuntimeError, match='outside the repository'):
            builder.release_signing_environment(signing_env(candidate), root)
    assert builder.release_signing_environment(signing_env(external), root) == external.resolve()


@pytest.mark.parametrize('ignored,tracked,allowed', [(True,False,True),(False,False,False),(True,True,False)])
def test_repository_signing_requires_ignored_and_untracked_key(builder,tmp_path,ignored,tracked,allowed):
    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git','init','--quiet',str(root)],check=True,capture_output=True)
    key = root / 'signing' / 'release.p12'
    key.parent.mkdir()
    key.write_text('synthetic test fixture; not a signing key')
    if ignored:
        (root / '.gitignore').write_text('/signing/\n')
    if tracked:
        subprocess.run(['git','add','--force','signing/release.p12'],cwd=root,check=True,capture_output=True)
    if allowed:
        assert builder.release_signing_environment(signing_env(key),root) == key.resolve()
    else:
        with pytest.raises(RuntimeError,match='Git-ignored and untracked'):
            builder.release_signing_environment(signing_env(key),root)


def test_local_signing_cannot_rely_on_git_check_failures(builder,tmp_path):
    root=tmp_path / 'not-a-repository'
    key=root / 'signing' / 'release.p12'
    key.parent.mkdir(parents=True)
    key.write_text('synthetic fixture')
    with pytest.raises(RuntimeError,match='Git-ignored and untracked'):
        builder.release_signing_environment(signing_env(key),root)


def signature_output(digest='a' * 64, name='CN=PG Proxy Release', modern=True):
    return (f'Verified using v2 scheme (APK Signature Scheme v2): {str(modern).lower()}\n'
            f'Signer #1 certificate DN: {name}\nSigner #1 certificate SHA-256 digest: {digest}\n')


@pytest.mark.parametrize('output', [
    signature_output(digest='b' * 64),
    signature_output(name='C=US, O=Android, CN=Android Debug'),
    signature_output(modern=False),
    signature_output() + 'Signer #2 certificate SHA-256 digest: ' + 'a' * 64 + '\n',
])
def test_release_signature_rejects_wrong_debug_legacy_or_multiple_signers(builder, output):
    with pytest.raises(RuntimeError):
        builder.validate_release_signature(output, 'a' * 64)


def test_release_signature_keeps_only_public_verified_identity(builder):
    result = builder.validate_release_signature(signature_output(), 'a' * 64)
    assert result == {'certificate_sha256': 'a' * 64, 'signature_verified': True, 'non_debug_certificate': True}


def sdk37_signature_output(count=1, digest='a' * 64, name='CN=PG Proxy, OU=Release, O=print-yuhuan'):
    return ('Verifies\n'
            'Verified using v1 scheme (JAR signing): false\n'
            'Verified using v2 scheme (APK Signature Scheme v2): false\n'
            'Verified using v3 scheme (APK Signature Scheme v3): true\n'
            'Verified using v3.1 scheme (APK Signature Scheme v3.1): false\n'
            'Verified using v3.2 scheme (APK Signature Scheme v3.2): false\n'
            f'Number of signers: {count}\nV3.0 Signer: certificate DN: {name}\n'
            f'V3.0 Signer: certificate SHA-256 digest: {digest}\n')


def test_release_signature_accepts_sdk37_v3_signer_label(builder):
    result = builder.validate_release_signature(sdk37_signature_output(), 'a' * 64)
    assert result == {'certificate_sha256': 'a' * 64, 'signature_verified': True, 'non_debug_certificate': True}


@pytest.mark.parametrize('output', [
    sdk37_signature_output(count=2),
    sdk37_signature_output(count=0),
    sdk37_signature_output(digest='b' * 64),
    sdk37_signature_output(name='CN=Android Debug, O=Android'),
    sdk37_signature_output().replace('Number of signers: 1\n', ''),
    signature_output() + 'Number of signers: 2\n',
])
def test_release_signature_does_not_weaken_sdk37_identity_checks(builder, output):
    with pytest.raises(RuntimeError):
        builder.validate_release_signature(output, 'a' * 64)


def manifest_output(name='dev.pgproxy.app', version='0.2.0', code='20', debug=False):
    return (f"package: name='{name}' versionCode='{code}' versionName='{version}'\n"
            "application-label:'PG Proxy'\n" + ('application-debuggable\n' if debug else ''))


@pytest.mark.parametrize('output', [
    manifest_output(name='dev.example.wrong'), manifest_output(version='0.0.1'),
    manifest_output(code='9'), manifest_output(debug=True), '',
])
def test_release_manifest_uses_actual_identity_and_debug_flag(builder, output):
    with pytest.raises(RuntimeError):
        builder.validate_release_manifest(output, '0.2.0', 20)


def test_release_manifest_accepts_non_debuggable_matching_package(builder):
    assert builder.validate_release_manifest(manifest_output(), '0.2.0', 20) == {
        'manifest_identity_verified': True, 'debuggable': False}


def test_certificate_export_passes_password_by_environment_only(builder, monkeypatch, tmp_path):
    env = signing_env(tmp_path / 'release.jks')
    def capture(command, actual_env, label, *, binary=False):
        assert binary
        assert actual_env is env
        assert 'synthetic-store-secret' not in command
        assert 'synthetic-key-secret' not in command
        assert '-storepass:env' in command
        assert 'PG_SIGNING_STORE_PASSWORD' in command
        return b'synthetic-public-certificate'
    monkeypatch.setattr(builder, 'capture_release_tool', capture)
    assert len(builder.configured_certificate_sha256(tmp_path / 'release.jks', Path('keytool'), env)) == 64


def test_release_rejects_instrumentation_before_running_tools(builder, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['build_app.py', '--build-type', 'release', '--tests'])
    monkeypatch.setattr(builder, 'run', lambda *_a, **_k: pytest.fail('No tools may run for invalid options'))
    with pytest.raises(SystemExit) as error:
        builder.main()
    assert error.value.code == 2
    assert 'Release artifacts cannot include instrumentation' in capsys.readouterr().err


@pytest.mark.parametrize('signature_valid', [True, False])
def test_release_package_only_revalidates_before_copying(builder, monkeypatch, tmp_path, signature_valid):
    root = tmp_path / 'repo'
    apk_dir = root / 'app/build/outputs/apk/arm64/release'
    apk_dir.mkdir(parents=True)
    apk = apk_dir / 'app-arm64-release.apk'
    apk.write_bytes(b'synthetic-apk-container')
    (apk_dir / 'output-metadata.json').write_text(json.dumps({
        'applicationId': 'dev.pgproxy.app', 'elements': [
            {'versionName': '0.2.0', 'versionCode': 20, 'outputFile': apk.name}]}))
    key = tmp_path / 'release.jks'
    key.write_text('synthetic signing fixture')
    for name, value in signing_env(key).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(builder, 'ROOT', root)
    monkeypatch.setattr(builder, 'local_sdk', lambda: tmp_path / 'sdk')
    monkeypatch.setattr(builder, 'local_ndk', lambda _sdk: None)
    monkeypatch.setattr(builder, 'executable', lambda *_a: tmp_path / 'java')
    monkeypatch.setattr(builder, 'release_verification_tools', lambda *_a: ('keytool', 'aapt2', 'apksigner'))
    monkeypatch.setattr(builder, 'configured_certificate_sha256', lambda *_a: 'a' * 64)
    monkeypatch.setattr(builder, 'run', lambda *_a, **_k: None)
    monkeypatch.setattr(builder, 'verify_apk', lambda path, abi: {'runtime_checked': path == apk and abi == 'arm64-v8a'})
    calls = []
    def verify(path, _java, _aapt, _signer, expected, version, code, _env):
        calls.append((path, expected, version, code))
        if not signature_valid:
            raise RuntimeError('Release APK signer does not match')
        return {'certificate_sha256': expected, 'signature_verified': True, 'debuggable': False}
    monkeypatch.setattr(builder, 'verify_release_apk', verify)
    monkeypatch.setattr(sys, 'argv', ['build_app.py', '--build-type', 'release', '--abi', 'arm64-v8a', '--package-only'])
    if not signature_valid:
        with pytest.raises(RuntimeError, match='signer does not match'):
            builder.main()
        assert not list((root / 'artifacts').glob('*.apk'))
    else:
        builder.main()
        result = json.loads((root / 'artifacts/pg-proxy-release-apks.json').read_text())
        assert result['build_invoked'] is False
        assert result['artifacts'][0]['build_type'] == 'release'
        assert result['artifacts'][0]['signing']['signature_verified']
        assert (root / 'artifacts/PG-Proxy-0.2.0-arm64-v8a-release.apk').read_bytes() == apk.read_bytes()
        assert not (root / 'artifacts/pg-proxy-apks.json').exists()
    assert calls == [(apk, 'a' * 64, '0.2.0', 20)]
