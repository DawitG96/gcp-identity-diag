"""FortiVPN helpers: start openfortivpn detached so the tunnel survives the GUI
closing, tail its logfile for live output, and detect/stop a session on reopen.

openfortivpn runs as root under sudo and is launched in its own session
(start_new_session=True), so it is not killed when the GUI process exits. On
reopen we find it again by name (pgrep) and read how long it has run (ps
etimes). Output is appended to LOG_FILE so it can be shown whether the session
was started this run or a previous one.
"""
import re
import subprocess
from pathlib import Path

_HOST_RE = re.compile(r'(?m)^\s*host\s*=')

LOG_FILE = Path.home() / '.cache' / 'fortivpn-gui.log'
PROC_NAME = 'openfortivpn'
CONNECTED_MARK = 'Tunnel is up and running'


def list_configs(directory):
    """Return sorted openfortivpn config files in directory: regular files whose
    content has a 'host = ...' setting (so junk like .deb/lock files is skipped)."""
    out = []
    try:
        entries = sorted(Path(directory).iterdir())
    except OSError:
        return out
    for p in entries:
        if not p.is_file():
            continue
        try:
            head = p.read_text(errors='replace')[:2048]
        except OSError:
            continue
        if _HOST_RE.search(head):
            out.append(p)
    return out


def find_pid():
    """PID of the running openfortivpn, or None. Exact-name match."""
    r = subprocess.run(['pgrep', '-x', PROC_NAME], capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.split()]
    return pids[0] if pids else None


def elapsed_seconds(pid):
    """Seconds `pid` has run (ps etimes), or None if gone."""
    r = subprocess.run(['ps', '-o', 'etimes=', '-p', str(pid)],
                       capture_output=True, text=True)
    s = r.stdout.strip()
    return int(s) if s.isdigit() else None


def fmt_elapsed(secs):
    """Seconds -> compact 'HHhMMm' / 'MMm' / 'SSs'."""
    if secs is None:
        return ''
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f'{h}h {m:02d}m'
    if m:
        return f'{m}m {s:02d}s'
    return f'{s}s'


def start(config_path, password, otp=''):
    """Launch openfortivpn detached (own session) appending to LOG_FILE, sudo
    password fed via stdin. Survives the GUI exiting. Returns nothing."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text('')  # fresh log per connect
    argv = ['sudo', '-S', '-p', '', PROC_NAME, '-c', config_path]
    if otp:
        argv += ['--otp', otp]
    log = open(LOG_FILE, 'a')
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True, text=True)
    proc.stdin.write((password or '') + '\n')
    proc.stdin.flush()
    proc.stdin.close()


def sudo_nopass():
    """True if sudoers lets us run pkill without a password."""
    return subprocess.run(['sudo', '-n', '-l', 'pkill'], capture_output=True).returncode == 0


def stop(password):
    """SIGINT openfortivpn for a clean tunnel teardown. Needs sudo password
    because it runs as root (the GUI may be a fresh process with no cached sudo)."""
    subprocess.run(['sudo', '-S', '-p', '', 'pkill', '-INT', '-x', PROC_NAME],
                   input=(password or '') + '\n', text=True, capture_output=True)


if __name__ == '__main__':
    assert fmt_elapsed(0) == '0s'
    assert fmt_elapsed(75) == '1m 15s'
    assert fmt_elapsed(3725) == '1h 02m', fmt_elapsed(3725)
    assert fmt_elapsed(None) == ''

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'vpn-a').write_text('host = 1.2.3.4\nport = 443\n')
        (Path(d) / 'notes.md').write_text('# just notes\n')
        (Path(d) / 'package-lock.json').write_text('{}')
        names = [p.name for p in list_configs(d)]
        assert names == ['vpn-a'], f'list_configs picked wrong files: {names}'
    print('vpn_utils self-check ok')
