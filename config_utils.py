import json
import subprocess
from pathlib import Path

import gcloud_utils

CONFIG_DIR = Path.home() / '.config' / 'gcp_diag'
CONFIG_FILE = CONFIG_DIR / 'accounts.json'
GCLOUD_DIR = Path.home() / '.config' / 'gcloud'
CA_CERT_PATH = Path.home() / '.config' / 'corp-root-ca.pem'


def load_config():
    """Return parsed config dict, or None if file missing/corrupt."""
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save_config(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def adc_path_for_label(label):
    return GCLOUD_DIR / f'adc-{label}.json'


def get_default_adc_key(accounts):
    """Return key of the account with is_default_adc=True, or None."""
    for key, info in accounts.items():
        if info.get('is_default_adc'):
            return key
    return None


def get_kubectl_account_key(accounts):
    """Return key of the account with is_default_adc=False (kubectl/Lens), or None."""
    for key, info in accounts.items():
        if not info.get('is_default_adc'):
            return key
    return None


def _list_gcloud_configs():
    """Return list of gcloud configuration dicts, or [] on any failure."""
    try:
        r = subprocess.run(
            ['gcloud', 'config', 'configurations', 'list', '--format=json'],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def _detect_from_existing_adc_files():
    """
    Strategy 1: scan ~/.config/gcloud/adc-*.json, resolve each one's real identity
    via tokeninfo, and match it to a project from any gcloud configuration that
    shares that account. This reuses whatever label (adc-<label>.json) is ALREADY
    wired into the user's SDK/CLI setup, so it never invents a new/wrong file path.
    Returns dict on success (exactly 2 distinct identities resolved), else None.
    """
    if not GCLOUD_DIR.exists():
        return None
    adc_files = sorted(GCLOUD_DIR.glob('adc-*.json'))
    if len(adc_files) < 2:
        return None

    configs = _list_gcloud_configs()
    project_by_account = {}
    for cfg in configs:
        core = cfg.get('properties', {}).get('core', {})
        acc, proj = core.get('account'), core.get('project')
        if acc and proj and acc not in project_by_account:
            project_by_account[acc] = proj

    found = {}
    for adc_file in adc_files:
        identity, err = gcloud_utils.get_adc_identity(adc_file)
        if err or not identity or identity in found:
            continue
        label = adc_file.stem.removeprefix('adc-')
        found[identity] = {
            'label':          label,
            'account':        identity,
            'project':        project_by_account.get(identity, ''),
            'adc_file':       str(adc_file),
            'is_default_adc': False,
        }
        if len(found) == 2:
            break

    if len(found) != 2 or any(not v['project'] for v in found.values()):
        return None

    accounts = {f'account_{k}': v for k, v in zip('ab', found.values())}
    _mark_default_adc(accounts)
    return accounts


def _detect_from_distinct_configs():
    """
    Strategy 2 (fallback, no adc-*.json files yet): group gcloud configurations
    by distinct account email — handles duplicate/leftover configuration names
    pointing at the same account (e.g. 'default' and 'work' both being the
    same email). Requires exactly 2 distinct accounts.
    """
    configs = _list_gcloud_configs()
    by_account = {}
    for cfg in configs:
        name = cfg.get('name', '')
        core = cfg.get('properties', {}).get('core', {})
        account, project = core.get('account', ''), core.get('project', '')
        if not name or not account or not project or account in by_account:
            continue
        by_account[account] = {'label': name, 'account': account, 'project': project}

    if len(by_account) != 2:
        return None

    accounts = {}
    for key, info in zip('ab', by_account.values()):
        accounts[f'account_{key}'] = {
            **info,
            'adc_file':       str(adc_path_for_label(info['label'])),
            'is_default_adc': False,
        }
    _mark_default_adc(accounts)
    return accounts


def _mark_default_adc(accounts):
    """Set is_default_adc=True on whichever account's adc file matches the
    default ADC file's bytes; falls back to the first account if none match."""
    default_adc = GCLOUD_DIR / 'application_default_credentials.json'
    if default_adc.exists():
        try:
            default_bytes = default_adc.read_bytes()
            for info in accounts.values():
                adc = Path(info['adc_file'])
                if adc.exists() and adc.read_bytes() == default_bytes:
                    info['is_default_adc'] = True
                    return
        except OSError:
            pass
    next(iter(accounts.values()))['is_default_adc'] = True


def load_network_config():
    """Return network section with defaults. Merges with existing config file."""
    raw = load_config() or {}
    net = raw.get('network', {})
    return {
        'hotspot_ssid': net.get('hotspot_ssid', ''),
        'proxy_host':   net.get('proxy_host', ''),
        'proxy_port':   int(net.get('proxy_port', 443)),
        'vpn_dir':      net.get('vpn_dir', str(Path.home() / 'Desktop' / 'vpn')),
    }


def save_network_config(net_cfg):
    raw = load_config() or {}
    raw['network'] = net_cfg
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(raw, indent=2))


def auto_detect_config():
    """
    Build accounts config without asking the user anything, in two passes:
    1. From existing ~/.config/gcloud/adc-*.json files (preferred — matches
       whatever your SDK/CLI setup is already using).
    2. From distinct accounts across ALL gcloud configurations, tolerating
       duplicate configuration names for the same account.
    Returns dict on success, None if neither strategy finds exactly 2 accounts.
    """
    return _detect_from_existing_adc_files() or _detect_from_distinct_configs()
