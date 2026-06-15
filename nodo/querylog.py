"""
Local query log → personalization. Records what you ask nodo so it can surface
*your* frequently-touched files. Stays in `.nodo/queries.log` — local-only, never
networked, never committed (keep `.nodo/` out of git or just this file), bounded.
"""
import json
import re
import time
from collections import Counter
from pathlib import Path

LOG_NAME = 'queries.log'
MAX_LINES = 1000


def record(out_dir, kind, target):
    """Append one query event. Never raises."""
    try:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / LOG_NAME
        lines = p.read_text(encoding='utf-8', errors='ignore').splitlines() if p.exists() else []
        lines.append(json.dumps({'t': int(time.time()), 'kind': kind, 'target': str(target)}))
        if len(lines) > MAX_LINES:
            lines = lines[-MAX_LINES:]
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    except Exception:
        pass


def frequent_files(out_dir, rels, n=6):
    """Return [(rel, count)] for the files you query most, mapped to real paths."""
    p = Path(out_dir) / LOG_NAME
    if not p.exists():
        return []
    rels = list(rels)
    relset = set(rels)
    by_base, by_stem = {}, {}
    for r in rels:
        by_base.setdefault(r.split('/')[-1], r)
        by_stem.setdefault(re.sub(r'\.[^./]+$', '', r.split('/')[-1]), r)
    counts = Counter()
    try:
        for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            try:
                tgt = json.loads(line).get('target', '') or ''
            except Exception:
                continue
            for tok in re.findall(r'[A-Za-z_][\w./-]*', tgt):
                if tok in relset:
                    counts[tok] += 1
                elif tok in by_base:
                    counts[by_base[tok]] += 1
                elif re.sub(r'\.[^./]+$', '', tok) in by_stem:
                    counts[by_stem[re.sub(r'\.[^./]+$', '', tok)]] += 1
    except Exception:
        return []
    return counts.most_common(n)
