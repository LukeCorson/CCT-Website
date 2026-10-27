import json
import re
from pathlib import Path

folder = Path(r'C:\Users\Luke\.codex\sessions\2026\10\26')
for log in folder.glob('*.jsonl'):
    for line in log.read_text(encoding='utf-8').splitlines():
        payload = json.loads(line).get('payload', {})
        if payload.get('type') != 'custom_tool_call':
            continue
        for match in re.finditer(r'tools\.apply_patch\(\s*("(?:[^"\\]|\\.)*")', payload.get('input', '')):
            patch = json.loads(match[1])
            marker = '*** Add File: recovery/recover_history.py\n'
            if marker not in patch:
                continue
            body = patch.split(marker, 1)[1].rsplit('\n*** End Patch', 1)[0]
            code = '\n'.join(x[1:] for x in body.splitlines() if x.startswith('+')) + '\n'
            code = code.replace("DEST = ROOT / 'recovery' / 'source'", "DEST = ROOT / 'recovery' / 'source-current'")
            code = code.replace("            if p.exists() and p.read_text(encoding='utf-8') != content:\n                raise ValueError('Conflicting addition: ' + str(p))\n", '')
            code = code.replace('events = []', 'events = []\nfailed_calls = set()')
            code = code.replace("        if payload.get('type') not in", "        if payload.get('type') == 'custom_tool_call_output':\n            output = json.dumps(payload.get('output', ''))\n            if 'Script failed' in output or 'verification failed' in output:\n                failed_calls.add(payload.get('call_id'))\n        if event['timestamp'] > '2026-10-27T16:40:00':\n            continue\n        if payload.get('type') not in")
            code = code.replace('    try:\n        apply(patch)', '    if call_id in failed_calls:\n        continue\n    try:\n        apply(patch)')
            Path('recovery/recover_history.py').write_text(code, encoding='utf-8')
            print('Recovered restoration utility')
            raise SystemExit(0)
raise RuntimeError('Restoration history not found')
