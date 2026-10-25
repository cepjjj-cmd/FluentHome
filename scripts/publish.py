#!/usr/bin/env python3
"""Prepare signed FluentHome release assets; publish only with --publish."""
import argparse, hashlib, json, os, re, shutil, subprocess, tempfile
from pathlib import Path

REPO = 'cepjjj-cmd/FluentHome'
LAUNCHER = ('com.sweetetsky.tvhome', '9119274e5ed9863c7ba58531a91a7d882b0f73d944db4581aff476479f20d110')
COMPANION = ('com.sweetetsky.fluenthome.setup', 'bac2e3c225bb66ac5bae90bfc88593f24ab9f083b7a61b5452b9b9572ddfb76b')

def run(*args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True).stdout

def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha256(f.read()).hexdigest()

def inspect(apk, tools, expected):
    apk = apk.resolve()
    badging = run(tools / 'aapt', 'dump', 'badging', apk)
    match = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", badging, re.M)
    sdk = re.search(r"^sdkVersion:'([0-9]+)'", badging, re.M)
    cert = re.findall(r'Signer #\d+ certificate SHA-256 digest: ([a-f0-9]+)', run(tools / 'apksigner', 'verify', '--print-certs', apk))
    if not match or not sdk or match[1] != expected[0] or cert != [expected[1]]:
        raise ValueError('Unexpected APK identity/signature: ' + apk.name)
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}(?:[-.][A-Za-z0-9]+)*', match[3]):
        raise ValueError('Unsupported version name')
    return {'package': match[1], 'versionCode': int(match[2]), 'versionName': match[3], 'minSdk': int(sdk[1]), 'sha256': digest(apk), 'certificateSha256': cert[0], 'size': apk.stat().st_size}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--companion', type=Path, required=True)
    parser.add_argument('--notes', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_HOME', os.environ.get('ANDROID_SDK_ROOT', ''))))
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    candidates = sorted((p for p in (args.sdk / 'build-tools').glob('*') if (p / 'aapt').is_file() and (p / 'apksigner').is_file()), key=lambda p: [int(x) for x in re.findall(r'\d+', p.name)])
    if not candidates:
        parser.error('Specify --sdk with Android SDK build-tools')
    launcher = inspect(args.launcher, candidates[-1], LAUNCHER)
    companion = inspect(args.companion, candidates[-1], COMPANION)
    if launcher['minSdk'] < 30 or companion['versionCode'] < 9:
        raise ValueError('Unsupported launcher SDK or Companion without online updates')
    notes = args.notes.read_text(encoding='utf-8').strip()
    if len(notes) > 8000:
        raise ValueError('Release notes must be under 8000 characters')
    tag = 'v' + launcher['versionName'] + '-setup.' + companion['versionName']
    directory = Path(__file__).resolve().parents[1] / 'release-staging' / tag
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.launcher, directory / 'FluentHome.apk')
    shutil.copy2(args.companion, directory / 'FluentHome-Setup.apk')
    manifest = dict(launcher, schemaVersion=1, url=f'https://github.com/{REPO}/releases/download/{tag}/FluentHome.apk', releaseNotes=notes)
    (directory / 'launcher.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assets = [directory / name for name in ['FluentHome.apk', 'FluentHome-Setup.apk', 'launcher.json']]
    (directory / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.name + '\n' for p in assets), encoding='utf-8')
    assets.append(directory / 'SHA256SUMS')
    print('Prepared:', directory)
    if not args.publish:
        return
    # Do not overwrite an existing release/tag or silently make an older launcher latest.
    existing = subprocess.run(['gh', 'release', 'view', tag, '--repo', REPO], capture_output=True)
    if existing.returncode == 0:
        raise ValueError('This release already exists: ' + tag)
    previous = subprocess.run(['gh', 'api', f'repos/{REPO}/releases/latest'], capture_output=True, text=True)
    if previous.returncode == 0:
        previous_tag = json.loads(previous.stdout)['tag_name']
        with tempfile.TemporaryDirectory() as temp:
            run('gh', 'release', 'download', previous_tag, '--repo', REPO, '--pattern', 'launcher.json', '--dir', temp)
            old = json.loads((Path(temp) / 'launcher.json').read_text())
            if old['versionCode'] > launcher['versionCode']:
                raise ValueError('Refusing to publish an older launcher as latest')
    elif '404' not in previous.stderr:
        raise RuntimeError(previous.stderr.strip() or 'Cannot verify the current GitHub release')
    title = 'FluentHome ' + launcher['versionName'] + ' · Setup ' + companion['versionName']
    run('gh', 'release', 'create', tag, '--repo', REPO, '--draft', '--title', title, '--notes-file', args.notes, *assets)
    run('gh', 'release', 'edit', tag, '--repo', REPO, '--draft=false', '--latest')
    print(f'https://github.com/{REPO}/releases/tag/{tag}')

if __name__ == '__main__':
    main()
