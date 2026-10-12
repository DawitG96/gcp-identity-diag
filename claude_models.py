"""Lettura/scrittura dei modelli Claude Code usati da ccvlp e da Claude Code.

Due destinazioni, tenute coerenti:
- ~/.claude/settings.json  -> chiave "model" (modello principale, modo ufficiale)
- ~/.config/claude-int-aziendale.sh  -> export ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL
  dentro _claude_llmproxy (solo se il file esiste; l'env ha precedenza per ccvlp).

Standalone, solo stdlib. Self-check: python3 claude_models.py
"""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / '.claude' / 'settings.json'
CLAUDE_SH = Path.home() / '.config' / 'claude-int-aziendale.sh'

# Modelli selezionabili (famiglia Claude 5). Aggiorna qui quando IT annuncia nuovi ID.
MODELS = [
    'claude-opus-5-5',
    'claude-sonnet-5-5',
    'claude-haiku-5-5',
    'claude-fable-5-1',
]

DEFAULT_MAIN = 'claude-opus-5-5'
DEFAULT_FAST = 'claude-sonnet-5-5'

MAIN_VAR = 'ANTHROPIC_MODEL'
FAST_VAR = 'ANTHROPIC_SMALL_FAST_MODEL'


def _parse_export(line, var):
    """Se `line` e' `export VAR="val"` (con indent/virgolette opzionali) ritorna val, else None."""
    s = line.strip()
    prefix = f'export {var}='
    if not s.startswith(prefix):
        return None
    return s[len(prefix):].strip().strip('"').strip("'")


def read_models():
    """Ritorna (main, fast). Precedenza: export in claude-int-aziendale.sh, poi settings.json, poi default."""
    main = fast = None
    if CLAUDE_SH.exists():
        for line in CLAUDE_SH.read_text(encoding='utf-8').splitlines():
            main = _parse_export(line, MAIN_VAR) or main
            fast = _parse_export(line, FAST_VAR) or fast
    if main is None:
        try:
            main = json.loads(SETTINGS_FILE.read_text()).get('model')
        except (OSError, json.JSONDecodeError):
            pass
    return main or DEFAULT_MAIN, fast or DEFAULT_FAST


def _upsert_export(text, var, value):
    """Rimpiazza la riga `export VAR=...` mantenendo l'indent; se assente, la inserisce
    prima di `unset NODE_TLS_REJECT_UNAUTHORIZED` (dentro _claude_llmproxy), altrimenti in coda."""
    new_line = f'  export {var}="{value}"'
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _parse_export(line, var) is not None:
            indent = line[:len(line) - len(line.lstrip())]
            lines[i] = f'{indent}export {var}="{value}"'
            return '\n'.join(lines) + ('\n' if text.endswith('\n') else '')
    for i, line in enumerate(lines):
        if line.strip() == 'unset NODE_TLS_REJECT_UNAUTHORIZED':
            lines.insert(i, new_line)
            return '\n'.join(lines) + ('\n' if text.endswith('\n') else '')
    lines.append(new_line.strip())
    return '\n'.join(lines) + '\n'


def save_models(main, fast):
    """Scrive main+fast dove possibile. Ritorna lista dei target aggiornati (per il log)."""
    written = []

    # settings.json: solo il modello principale (chiave ufficiale)
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    if data.get('model') != main:
        data['model'] = main
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2) + '\n')
        written.append(str(SETTINGS_FILE))

    # claude-int-aziendale.sh: entrambi gli export (solo se il file esiste)
    if CLAUDE_SH.exists():
        text = CLAUDE_SH.read_text(encoding='utf-8')
        new = _upsert_export(_upsert_export(text, MAIN_VAR, main), FAST_VAR, fast)
        if new != text:
            CLAUDE_SH.write_text(new, encoding='utf-8')
            written.append(str(CLAUDE_SH))

    return written


def _demo():
    # upsert: rimpiazza esistente mantenendo indent
    sh = ('_claude_llmproxy() {\n'
          '  export ANTHROPIC_MODEL="old-main"\n'
          '  export NODE_EXTRA_CA_CERTS="$TIM_ROOT_CA"\n'
          '  unset NODE_TLS_REJECT_UNAUTHORIZED\n'
          '}\n')
    out = _upsert_export(sh, MAIN_VAR, 'new-main')
    assert '  export ANTHROPIC_MODEL="new-main"\n' in out, out
    assert 'old-main' not in out
    # upsert: inserisce mancante prima di unset, non in coda
    out = _upsert_export(sh, FAST_VAR, 'fast-x')
    assert '  export ANTHROPIC_SMALL_FAST_MODEL="fast-x"\n  unset NODE_TLS_REJECT_UNAUTHORIZED' in out, out
    # parse robusto a virgolette singole e indent
    assert _parse_export("  export ANTHROPIC_MODEL='v'", MAIN_VAR) == 'v'
    assert _parse_export('export FOO=1', MAIN_VAR) is None
    print('OK')


if __name__ == '__main__':
    _demo()
