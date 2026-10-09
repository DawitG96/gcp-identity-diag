import glob
import socket
import subprocess


def get_interfaces():
    """Return list of (name, [addr, ...]) for UP interfaces."""
    result = subprocess.run(['ip', '-brief', 'addr'], capture_output=True, text=True)
    ifaces = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == 'UP':
            ifaces.append((parts[0], parts[2:]))
    return ifaces


def get_default_routes():
    """Return list of (dev, gateway) for each default route."""
    result = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True)
    routes = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        gw = parts[parts.index('via') + 1] if 'via' in parts else '?'
        dev = parts[parts.index('dev') + 1] if 'dev' in parts else '?'
        routes.append((dev, gw))
    return routes


def get_wifi_ssid():
    """Return current WiFi SSID, or None if not connected / tool unavailable."""
    for cmd, parse in [
        (['iwgetid', '-r'],
         lambda out: out.strip() or None),
        (['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'],
         lambda out: next((l[4:] for l in out.splitlines() if l.startswith('yes:')), None)),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                ssid = parse(r.stdout)
                if ssid:
                    return ssid
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def get_connection_routes(ssid, proxy_host):
    """Return list of gateway IPs already set for proxy_host/32 in the nmcli connection profile."""
    try:
        r = subprocess.run(
            ['nmcli', '-g', 'ipv4.routes', 'connection', 'show', ssid],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        prefix = f'{proxy_host}/32'
        gateways = []
        for entry in r.stdout.strip().split(','):
            entry = entry.strip()
            if entry.startswith(prefix):
                parts = entry.split()
                if len(parts) >= 2:
                    gateways.append(parts[1])
        return gateways
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def get_wifi_iface():
    """Return name of the first wireless interface, or None (no external tools)."""
    for path in sorted(glob.glob('/sys/class/net/*/wireless')):
        return path.split('/')[-2]
    return None


def get_wifi_gateway(iface=None):
    """Return default gateway IP for the wifi interface from ip route, or None."""
    iface = iface or get_wifi_iface()
    if not iface:
        return None
    r = subprocess.run(['ip', 'route', 'show', 'default', 'dev', iface],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split()
        if 'via' in parts:
            return parts[parts.index('via') + 1]
    return None


def test_connectivity(host, port=443, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
