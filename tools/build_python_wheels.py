"""Build the missing pydantic-core wheel for Chaquopy's Android CPython.

Windows host: Python 3.11+, Visual Studio C++ tools, Android NDK r28c, Rust 1.99.1.
Downloads are exclusively from PyPI, Maven Central/Chaquopy and Rust's official CDN.
No Termux package or binary is reused. See docs/PYTHON_RUNTIME.md.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VERSION = '2.50.1'
PYTHON_TARGET = '3.14.8-0'
RUST = '1.99.1'
SOURCE_URL = 'https://files.pythonhosted.org/packages/a6/24/af4ec4be49fbc810f35b0bdcacc3d433b56bbfa469fd3bfd4ae116cd9bf1/pydantic_core-2.50.1.tar.gz'
SOURCE_SHA256 = 'e50d7b94baac6c7d09927fa5ca5800a0c7ee5015c7fcff65beb3a1931b5a6e09'
ABIS = {'arm64-v8a':('aarch64-linux-android','arm64_v8a'), 'x86_64':('x86_64-linux-android','x86_64')}
TARGET_HASHES = {'arm64-v8a':'13e1b17b2bd52983e13bf830a47317109f51a00ed23ecd91e8bc7d007c3f9033',
                 'x86_64':'1708385fd325d69efd95b97c2c0006840d2f6a756426e6c20b0dd6fa1bdcfe0f'}


def download(url, path, expected=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        pending = path.with_suffix(path.suffix+'.part')
        with urllib.request.urlopen(url,timeout=60) as response, pending.open('wb') as target:
            shutil.copyfileobj(response,target)
        pending.replace(path)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and digest != expected:
        raise RuntimeError(f'Hash mismatch: {path.name}')
    return digest


def run(args, **kwargs):
    print('RUN',args[0],*args[1:4],flush=True)
    subprocess.run([str(x) for x in args],check=True,**kwargs)


def visual_studio_env(env):
    # Cargo's host build scripts require the Windows linker and SDK libraries.
    program_files = Path(os.environ.get('ProgramFiles','C:/Program Files'))
    candidates = list((program_files/'Microsoft Visual Studio').glob('*/**/VC/Auxiliary/Build/vcvars64.bat'))
    if not candidates:
        candidates = list(Path('C:/Program Files (x86)/Microsoft Visual Studio').glob('*/**/VC/Auxiliary/Build/vcvars64.bat'))
    if not candidates:
        raise RuntimeError('Install the Visual Studio Desktop development with C++ workload')
    result = subprocess.run(f'cmd.exe /d /s /c ""{candidates[-1]}" >nul && set"',
                            check=True,capture_output=True,env=env)
    for line in result.stdout.decode('utf-8',errors='replace').splitlines():
        name,sep,value=line.partition('=')
        if sep and name: env[name]=value
    return env


def target(work,abi):
    destination=work/'targets'/abi
    archive_name=f'target-{PYTHON_TARGET}-{abi}.zip'
    cache=Path.home()/'.gradle/caches/modules-2/files-2.1/com.chaquo.python/target'/PYTHON_TARGET
    cached=list(cache.glob('*/'+archive_name))
    archive=cached[0] if cached else work/'targets'/archive_name
    if not archive.exists():
        relative=f'com/chaquo/python/target/{PYTHON_TARGET}/{archive_name}'
        try:
            download('https://repo.maven.apache.org/maven2/'+relative,archive)
        except urllib.error.HTTPError as error:
            if error.code!=404: raise
            download('https://chaquo.com/maven/'+relative,archive)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest!=TARGET_HASHES[abi]: raise RuntimeError('Unexpected Chaquopy target archive hash')
    if not (destination/'jniLibs'/abi/'libpython3.14.so').is_file():
        with zipfile.ZipFile(archive) as source: source.extractall(destination)
    return destination,digest


def package(source,binary,abi,output):
    tag=f'cp314-cp314-android_28_{ABIS[abi][1]}'
    info=f'pydantic_core-{VERSION}.dist-info'
    content={}
    for file in (source/'python/pydantic_core').rglob('*'):
        if file.is_file() and '__pycache__' not in file.parts:
            content[file.relative_to(source/'python').as_posix()]=file.read_bytes()
    content['pydantic_core/_pydantic_core.so']=binary.read_bytes()
    content[info+'/METADATA']=(source/'PKG-INFO').read_bytes()
    content[info+'/WHEEL']=(f'Wheel-Version: 1.0\nGenerator: pgproxy-android-builder\nRoot-Is-Purelib: false\nTag: {tag}\n').encode()
    content[info+'/licenses/LICENSE']=(source/'LICENSE').read_bytes()
    records=io.StringIO(newline='')
    writer=csv.writer(records)
    for name,data in sorted(content.items()):
        digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
        writer.writerow([name,'sha256='+digest,len(data)])
    writer.writerow([info+'/RECORD','',''])
    content[info+'/RECORD']=records.getvalue().encode()
    output.mkdir(parents=True,exist_ok=True)
    wheel=output/f'pydantic_core-{VERSION}-{tag}.whl'
    with zipfile.ZipFile(wheel,'w',zipfile.ZIP_DEFLATED) as result:
        for name,data in sorted(content.items()):
            entry=zipfile.ZipInfo(name,date_time=(2026,10,29,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=0o644<<16
            result.writestr(entry,data)
    return wheel


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--abi',choices=[*ABIS,'all'],default='all')
    parser.add_argument('--ndk',type=Path,default=Path(os.environ.get('LOCALAPPDATA',''))/'Android/Sdk/ndk/28.2.13676358')
    parser.add_argument('--bootstrap-rust',action='store_true')
    parser.add_argument('--toolchain',default=RUST)
    parser.add_argument('--cargo-home',type=Path,default=os.environ.get('CARGO_HOME'))
    parser.add_argument('--rustup-home',type=Path,default=os.environ.get('RUSTUP_HOME'))
    args=parser.parse_args()
    if os.name!='nt': parser.error('This reproducible builder currently targets a Windows build host')
    properties=args.ndk/'source.properties'
    if not properties.is_file(): parser.error('Android NDK source.properties not found')
    ndk_revision=next((line.split('=',1)[1].strip() for line in properties.read_text().splitlines()
                       if line.startswith('Pkg.Revision =')),None)
    if ndk_revision!='28.2.13676358': parser.error('The validated build requires Android NDK 28.2.13676358')
    work=ROOT/'data/python-wheels'
    work.mkdir(parents=True,exist_ok=True)
    cargo_home=(args.cargo_home or work/'cargo').resolve()
    rustup_home=(args.rustup_home or work/'rustup').resolve()
    env={**os.environ,'RUSTUP_HOME':str(rustup_home),'CARGO_HOME':str(cargo_home),
         'CARGO_REGISTRIES_CRATES_IO_PROTOCOL':'sparse','RUSTUP_TOOLCHAIN':args.toolchain}
    cargo_bin=cargo_home/'bin'
    if args.bootstrap_rust:
        installer=work/'rustup-init.exe'
        download('https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe',installer)
        run([installer,'-y','--no-modify-path','--profile','minimal','--default-toolchain',RUST],env=env)
        run([cargo_bin/'rustup.exe','target','add','--toolchain',RUST,*[v[0] for v in ABIS.values()]],env=env)
    env=visual_studio_env(env)
    env['PATH']=str(cargo_bin)+os.pathsep+env.get('Path',env.get('PATH',''))
    # Python's subprocess uses a case-insensitive environment on Windows.
    env.pop('Path',None)
    version=subprocess.check_output([str(cargo_bin/'rustc.exe'),'--version'],env=env,text=True).strip()
    if not version.startswith('rustc '+RUST+' '): raise RuntimeError('Rust '+RUST+' is required for the pinned build')
    source_archive=work/f'pydantic_core-{VERSION}.tar.gz'
    download(SOURCE_URL,source_archive,SOURCE_SHA256)
    source=work/f'pydantic_core-{VERSION}'
    if not source.exists():
        with tarfile.open(source_archive) as archive: archive.extractall(work,filter='data')
    if 'git+' in (source/'Cargo.lock').read_text():
        raise RuntimeError('Unexpected Git dependency: review networking before building')
    ndk_bin=args.ndk/'toolchains/llvm/prebuilt/windows-x86_64/bin'
    if not (ndk_bin/'clang.exe').is_file(): raise RuntimeError('NDK toolchain not found')
    report={'pydantic_core':VERSION,'python_target':PYTHON_TARGET,'rust':version,
            'source_sha256':SOURCE_SHA256,'ndk':ndk_revision,'artifacts':[]}
    report_file=ROOT/'artifacts/wheels/build-report.json'
    if report_file.is_file():
        previous=json.loads(report_file.read_text())
        if previous.get('source_sha256')==SOURCE_SHA256 and previous.get('rust')==version:
            report['artifacts']=previous.get('artifacts',[])
    for abi in (ABIS if args.abi=='all' else [args.abi]):
        triple,_=ABIS[abi]
        headers,target_sha=target(work,abi)
        lib=headers/'jniLibs'/abi
        configuration=headers/'pyo3-config.txt'
        config_text=('implementation=CPython\nversion=3.14\nshared=true\nabi3=false\n'
            f'lib_name=python3.14\nlib_dir={lib}\npointer_width=64\nsuppress_build_script_link_lines=false\n')
        if not configuration.is_file() or configuration.read_text()!=config_text:
            configuration.write_text(config_text)
        compile_env={**env,'PYO3_CONFIG_FILE':str(configuration),'PYO3_CROSS':'1',
            'CARGO_TARGET_'+triple.upper().replace('-','_')+'_LINKER':str(ndk_bin/(triple+'28-clang.cmd')),
            'RUSTFLAGS':'-C force-frame-pointers=yes -C link-arg=-Wl,-z,max-page-size=16384',
            'CC_'+triple.replace('-','_'):str(ndk_bin/(triple+'28-clang.cmd')),
            'AR_'+triple.replace('-','_'):str(ndk_bin/'llvm-ar.exe')}
        run([cargo_bin/'cargo.exe','build','--locked','--release','--target',triple,
             '--features','pyo3/extension-module','--lib'],cwd=source,env=compile_env)
        binary=source/'target'/triple/'release/lib_pydantic_core.so'
        dynamic=subprocess.check_output([str(ndk_bin/'llvm-readelf.exe'),'-d',str(binary)],text=True)
        program=subprocess.check_output([str(ndk_bin/'llvm-readelf.exe'),'-lW',str(binary)],text=True)
        if 'com.termux' in dynamic or 'RUNPATH' in dynamic or 'RPATH' in dynamic:
            raise RuntimeError('Unexpected app-specific native library dependency')
        if 'libpython3.14.so' not in dynamic: raise RuntimeError('Missing explicit Chaquopy libpython dependency')
        loads=[line.split() for line in program.splitlines() if line.strip().startswith('LOAD ')]
        if not loads or any(int(line[-1],16)<16384 for line in loads):
            raise RuntimeError('Native library lacks 16 KB load-segment alignment')
        wheel=package(source,binary,abi,ROOT/'artifacts/wheels')
        record={'abi':abi,'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
                'bytes':wheel.stat().st_size,'python_target_sha256':target_sha,
                'elf_dynamic':dynamic,'elf_program_headers':program,'apk_import_verified':False}
        report['artifacts']=[item for item in report['artifacts'] if item['abi']!=abi]+[record]
        print('BUILT',wheel,flush=True)
    report_file.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    main()
