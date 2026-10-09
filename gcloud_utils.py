import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
GCLOUD_DIR = HOME / '.config' / 'gcloud'
DEFAULT_ADC = GCLOUD_DIR / 'application_default_credentials.json'
PLUGIN_CACHE = HOME / '.kube' / 'gke_gcloud_auth_plugin_cache'
LENS_DESKTOP = HOME / '.local' / 'share' / 'applications' / 'lens-desktop.desktop'


def get_config_info(config_name):
    """Return (account, project) for a gcloud configuration (non-destructive)."""
    result = subprocess.run(
        ['gcloud', 'config', 'configurations', 'describe', config_name, '--format=json'],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout)
        core = data.get('properties', {}).get('core', {})
        return core.get('account', 'N/A'), core.get('project', 'N/A')
    except (json.JSONDecodeError, KeyError):
        return 'N/A', 'N/A'


def get_adc_identity(adc_path):
    """
    Return (email, None) or (None, error_str).
    Spawns gcloud in a subprocess with GOOGLE_APPLICATION_CREDENTIALS overridden;
    never touches the parent process environment.
    """
    env = {**os.environ, 'GOOGLE_APPLICATION_CREDENTIALS': str(adc_path)}
    result = subprocess.run(
        ['gcloud', 'auth', 'application-default', 'print-access-token'],
        capture_output=True, text=True, env=env, timeout=15,
    )
    if result.returncode != 0:
        return None, (result.stderr.strip() or 'print-access-token failed')
    token = result.stdout.strip()
    if not token:
        return None, 'empty token'
    try:
        url = f'https://oauth2.googleapis.com/tokeninfo?access_token={token}'
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read())
            return data.get('email', 'unknown'), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        return None, f'tokeninfo HTTP {e.code}: {body[:120]}'
    except Exception as e:
        return None, str(e)


def get_kubectl_identity():
    """
    Return (email, None) or (None, error_str).
    Runs gke-gcloud-auth-plugin with minimal KUBERNETES_EXEC_INFO, resolves email via tokeninfo.
    """
    exec_info = json.dumps({
        'apiVersion': 'client.authentication.k8s.io/v1beta1',
        'kind': 'ExecCredential',
        'spec': {'interactive': False},
    })
    env = {**os.environ, 'KUBERNETES_EXEC_INFO': exec_info}
    r = subprocess.run(['gke-gcloud-auth-plugin'], capture_output=True, text=True, env=env, timeout=15)
    if r.returncode != 0:
        return None, r.stderr.strip() or 'gke-gcloud-auth-plugin failed'
    try:
        token = json.loads(r.stdout)['status']['token']
    except (json.JSONDecodeError, KeyError) as e:
        return None, f'parse error: {e}'
    try:
        url = f'https://oauth2.googleapis.com/tokeninfo?access_token={token}'
        with urllib.request.urlopen(url, timeout=8) as resp:
            info = json.loads(resp.read())
            return info.get('email', 'unknown'), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='replace')
        return None, f'tokeninfo HTTP {e.code}: {body[:120]}'
    except Exception as e:
        return None, str(e)


def default_adc_matches(adc_path):
    """True if DEFAULT_ADC has identical byte content to the given adc_path."""
    try:
        return DEFAULT_ADC.read_bytes() == Path(adc_path).read_bytes()
    except OSError:
        return False


def get_plugin_cache_info():
    """Return (exists: bool, mtime_str: str | None)."""
    if not PLUGIN_CACHE.exists():
        return False, None
    import datetime
    ts = datetime.datetime.fromtimestamp(PLUGIN_CACHE.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
    return True, ts


def activate_config(name):
    return subprocess.run(
        ['gcloud', 'config', 'configurations', 'activate', name],
        capture_output=True, text=True,
    )


def check_adc_token_valid(adc_path):
    env = {**os.environ, 'GOOGLE_APPLICATION_CREDENTIALS': str(adc_path)}
    r = subprocess.run(
        ['gcloud', 'auth', 'application-default', 'print-access-token'],
        capture_output=True, text=True, env=env, timeout=15,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def sync_default_adc_to_account(expected_email, dest_adc_path):
    """
    Copy DEFAULT_ADC → dest_adc_path only when DEFAULT_ADC resolves to expected_email.
    Return (ok: bool, message: str).
    """
    if not DEFAULT_ADC.exists():
        return False, f'{DEFAULT_ADC} non esiste'
    identity, err = get_adc_identity(DEFAULT_ADC)
    if err:
        return False, f'Errore token default ADC: {err}'
    if identity != expected_email:
        return False, f'Identità default ADC è {identity!r}, attesa {expected_email!r} — skip copia'
    shutil.copy2(str(DEFAULT_ADC), str(dest_adc_path))
    return True, f'Copiato {DEFAULT_ADC.name} → {Path(dest_adc_path).name} (identità: {identity})'


def get_lens_desktop_content(adc_path):
    return (
        '[Desktop Entry]\n'
        'Name=Lens\n'
        'Comment=Kubernetes IDE\n'
        f'Exec=env GOOGLE_APPLICATION_CREDENTIALS={adc_path} /opt/Lens/lens-desktop %U\n'
        'Icon=lens-desktop\n'
        'Terminal=false\n'
        'Type=Application\n'
        'Categories=Development;\n'
    )


def silent_refresh_adc(adc_path):
    """
    Try to refresh ADC token silently using the existing refresh_token.
    Returns (ok: bool, message: str).
    If ok=False and message contains 'Login ADC', a full browser login is required.
    """
    env = {**os.environ, 'GOOGLE_APPLICATION_CREDENTIALS': str(adc_path)}
    try:
        r = subprocess.run(
            ['gcloud', 'auth', 'application-default', 'print-access-token'],
            capture_output=True, text=True, env=env, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False, 'Timeout — rete assente o gcloud bloccato'
    if r.returncode == 0 and r.stdout.strip():
        return True, 'Token valido / rinnovato silenziosamente'
    err = r.stderr.strip()
    if any(w in err.lower() for w in ('invalid_grant', 'expired', 'revoked', 'reauth')):
        return False, f'Refresh token scaduto — necessario Login ADC. ({err[:80]})'
    return False, f'Errore: {err[:120]}'


def ensure_lens_desktop(adc_path):
    """
    Create or overwrite the Lens .desktop override if missing or different from expected.
    Return (changed: bool, message: str).
    """
    expected = get_lens_desktop_content(adc_path)
    if LENS_DESKTOP.exists() and LENS_DESKTOP.read_text() == expected:
        return False, f'{LENS_DESKTOP} già corretto, nessuna modifica'
    LENS_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    LENS_DESKTOP.write_text(expected)
    return True, f'Scritto {LENS_DESKTOP}'
