"""Run a command under a pseudo-terminal so we can show its live output in the
GUI, feed it input (sudo password / OTP), and disconnect it cleanly.

openfortivpn runs as root under sudo. A normal user cannot SIGTERM a root
process, but the pty line discipline can: writing Ctrl-C (\\x03) to the master
delivers SIGINT to the foreground process group of the slave tty — exactly like
pressing Ctrl-C in a real terminal. That is how disconnect works here.
"""
import os
import pty
import re
import threading
from pathlib import Path

_HOST_RE = re.compile(r'(?m)^\s*host\s*=')


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


class PtyProcess:
    def __init__(self, argv, on_output, on_exit):
        """argv: command list. on_output(str) and on_exit(int returncode) are
        called from a background reader thread — marshal to the UI thread
        yourself (e.g. root.after)."""
        self.argv = argv
        self._on_output = on_output
        self._on_exit = on_exit
        self.pid = None
        self._fd = None

    def start(self):
        pid, fd = pty.fork()
        if pid == 0:  # child: becomes the pty slave, with the slave as its ctty
            try:
                os.execvp(self.argv[0], self.argv)
            except OSError:
                os._exit(127)
        self.pid = pid
        self._fd = fd
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while True:
                try:
                    data = os.read(self._fd, 4096)
                except OSError:
                    break  # slave closed
                if not data:
                    break
                self._on_output(data.decode('utf-8', 'replace'))
        finally:
            try:
                _, status = os.waitpid(self.pid, 0)
                rc = os.waitstatus_to_exitcode(status)
            except OSError:
                rc = -1
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._on_exit(rc)

    def write(self, text: str):
        if self._fd is not None:
            os.write(self._fd, text.encode())

    def send_sigint(self):
        """Ctrl-C to the foreground process group via the pty line discipline."""
        self.write('\x03')

    def is_running(self) -> bool:
        return self._fd is not None


if __name__ == '__main__':  # ponytail: self-check without root — pipe a child's output through the pty
    import time
    out, done = [], []
    p = PtyProcess(['bash', '-c', 'echo hello-pty; exit 7'],
                   on_output=out.append, on_exit=done.append)
    p.start()
    for _ in range(50):
        if done:
            break
        time.sleep(0.05)
    assert done, 'process never exited'
    assert 'hello-pty' in ''.join(out), f'output not captured: {out!r}'
    assert done[0] == 7, f'wrong returncode: {done[0]}'

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'vpn-a').write_text('host = 1.2.3.4\nport = 443\n')
        (Path(d) / 'notes.md').write_text('# just notes\n')
        (Path(d) / 'package-lock.json').write_text('{}')
        names = [p.name for p in list_configs(d)]
        assert names == ['vpn-a'], f'list_configs picked wrong files: {names}'
    print('vpn_utils self-check ok')
