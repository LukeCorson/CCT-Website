"""Recover project patches into a separate directory; never execute history commands."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'recovery' / 'source-current'
HISTORY = Path(r'C:\Users\Luke\.codex\sessions\2026\10\26')
PREFIX = str(ROOT).replace('\\', '/').lower() + '/'

def target(raw):
    raw = raw.replace('\\', '/')
    if raw.lower().startswith(PREFIX):
        raw = raw[len(PREFIX):]
    p = Path(raw)
    if p.is_absolute() or ':' in raw or '..' in p.parts:
        raise ValueError('Outside project: ' + raw)
    return DEST / p

def apply(patch):
    lines = patch.splitlines()
    i = 1
    while i < len(lines):
        header = lines[i]
        i += 1
        if header == '*** End Patch':
            break
        if header.startswith('*** Add File: '):
            p = target(header[14:])
            body = []
            while i < len(lines) and not lines[i].startswith('*** '):
                if not lines[i].startswith('+'):
                    raise ValueError('Bad addition')
                body.append(lines[i][1:]); i += 1
            content = '\n'.join(body) + '\n'
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8')
        elif header.startswith('*** Update File: '):
            p = target(header[17:])
            content = p.read_text(encoding='utf-8').splitlines()
            move = None
            cursor = 0
            if i < len(lines) and lines[i].startswith('*** Move to: '):
                move = target(lines[i][13:]); i += 1
            while i < len(lines) and not lines[i].startswith('*** '):
                if not lines[i].startswith('@@'):
                    raise ValueError('Expected hunk: ' + lines[i])
                anchor = lines[i][3:] if lines[i].startswith('@@ ') else ''
                i += 1
                if anchor:
                    matches = [n for n in range(cursor, len(content)) if content[n] == anchor]
                    if matches:
                        cursor = matches[0] + 1
                old, new = [], []
                while i < len(lines) and not lines[i].startswith(('@@', '*** ')):
                    line = lines[i]; i += 1
                    if line.startswith(' '):
                        old.append(line[1:]); new.append(line[1:])
                    elif line.startswith('-'):
                        old.append(line[1:])
                    elif line.startswith('+'):
                        new.append(line[1:])
                    elif line == '':
                        old.append(''); new.append('')
                    else:
                        raise ValueError('Bad hunk')
                eof = i < len(lines) and lines[i] == '*** End of File'
                if eof:
                    i += 1
                found = None
                for relaxed in (False, True):
                    for n in range(cursor, len(content) - len(old) + 1):
                        candidate = content[n:n+len(old)]
                        equal = candidate == old if not relaxed else [x.rstrip() for x in candidate] == [x.rstrip() for x in old]
                        if equal and (not eof or n + len(old) == len(content)):
                            found = n; break
                    if found is not None:
                        break
                if found is None:
                    raise ValueError('Hunk not found: ' + str(p) + ' ' + repr(old[:2]))
                content[found:found+len(old)] = new
                cursor = found + len(new)
            p.write_text('\n'.join(content) + '\n', encoding='utf-8')
            if move:
                move.parent.mkdir(parents=True, exist_ok=True)
                p.rename(move)
        elif header.startswith('*** Delete File: '):
            target(header[17:]).unlink(missing_ok=True)
        else:
            raise ValueError('Unknown patch header: ' + header)

events = []
failed_calls = set()
for logfile in HISTORY.glob('*.jsonl'):
    for line in logfile.read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        payload = event.get('payload', {})
        if payload.get('type') == 'custom_tool_call_output':
            output = json.dumps(payload.get('output', ''))
            if 'Script failed' in output or 'verification failed' in output:
                failed_calls.add(payload.get('call_id'))
        if event['timestamp'] > '2026-10-27T16:40:00':
            continue
        if payload.get('type') not in ('custom_tool_call', 'function_call'):
            continue
        code = payload.get('input', payload.get('arguments', ''))
        for match in re.finditer(r'tools\.apply_patch\(\s*("(?:[^"\\]|\\.)*")', code):
            patch = json.loads(match[1])
            if patch.startswith('*** Begin Patch'):
                events.append((event['timestamp'], payload.get('call_id'), patch))
        if payload.get('name', '').endswith('apply_patch') and code.startswith('*** Begin Patch'):
            events.append((event['timestamp'], payload.get('call_id'), code))

DEST.mkdir(parents=True, exist_ok=True)
report = {'applied': [], 'failed': []}
for timestamp, call_id, patch in sorted(events):
    if call_id in failed_calls:
        continue
    try:
        apply(patch)
        report['applied'].append({'time': timestamp, 'call_id': call_id})
    except Exception as exc:
        report['failed'].append({'time': timestamp, 'call_id': call_id, 'error': str(exc)})
(ROOT / 'recovery' / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({'patches': len(events), 'applied': len(report['applied']), 'failed': report['failed'], 'files': len(list(DEST.rglob('*.*')))}, indent=2))
