"""
`--ask "<question>"` — one natural-language entry point for every query.

Routes a plain-English question to the right primitive so a developer (or an
agent) never has to remember which flag to use — they just ask nodo. Pure
heuristics over the existing graph/symbol index; zero-dependency, deterministic.

  - "what breaks if I change lib/db.ts?"     → blast radius + change impact
  - "how does middleware connect to the db?" → import-path trace
  - "who calls verifyToken?"                 → symbol definition + references
  - "where is authentication handled?"       → concept search (code + docs + PDFs)
  - "what are the main topics / overview?"   → knowledge-graph topics
"""
import json
import re
from pathlib import Path

from .query import query_file, path_between, explain_concept
from .symbols import build_symbol_index

_CONNECT = re.compile(r'\b(connect|connects|connected|reach|reaches|between|relate|related|'
                      r'link|linked|flow|flows|wire|wired|depend on)\b', re.I)
_IMPACT = re.compile(r'\b(break|breaks|broke|impact|affect|affects|changing|blast|radius|'
                     r'ripple|safe to change|consequence)\b', re.I)
_ISSUE = re.compile(r'\b(issues?|bugs?|problems?|wrong|smells?|vulnerab|insecure|risks?|'
                    r'todo|fixme|fix|dead\s*code|lint|broken|security|audit|anything\s+bad)\b', re.I)
_HUB = re.compile(r'\b(hubs?|central|core\s+files?|important\s+files?|main\s+files?|'
                  r'load.bearing|most\s+connected|biggest|key\s+files?|god\s*nodes?)\b', re.I)
_TOPIC = re.compile(r'\b(topics?|overview|architecture|high.level|summary|structure|'
                    r'what\s+is\s+this|map\s+of|what.?s\s+in\s+(this|the))\b', re.I)
# words to drop when falling back to concept search
_QWORDS = {'what', 'where', 'which', 'who', 'how', 'why', 'when', 'does', 'do', 'is', 'are',
           'the', 'a', 'an', 'of', 'to', 'in', 'on', 'for', 'and', 'or', 'i', 'my', 'me',
           'this', 'that', 'it', 'be', 'can', 'should', 'would', 'if', 'change', 'changing',
           'break', 'breaks', 'affect', 'affects', 'happen', 'happens', 'use', 'uses', 'used',
           'call', 'calls', 'about', 'handle', 'handled', 'handles', 'work', 'works', 'file'}


def _resolve_files_and_symbols(question, nodes, symbols_set):
    """Pull file paths and known symbols mentioned in the question."""
    rels = [n['rel'] for n in nodes]
    relset = set(rels)
    by_base, by_stem = {}, {}
    for r in rels:
        b = r.split('/')[-1]
        by_base.setdefault(b, r)
        by_stem.setdefault(re.sub(r'\.[^./]+$', '', b), r)
    files, syms = [], []
    for t in re.findall(r'[A-Za-z_][\w./-]*', question):
        if t in relset:
            files.append(t)
        elif '/' in t and any(r.endswith(t) for r in rels):
            files.append(next(r for r in rels if r.endswith(t)))
        elif t in by_base:
            files.append(by_base[t])
        elif t in by_stem and len(t) >= 2 and t.lower() not in _QWORDS:
            files.append(by_stem[t])
        elif t in symbols_set:
            syms.append(t)
    # de-dup preserving order
    return list(dict.fromkeys(files)), list(dict.fromkeys(syms))


def _ctx(out_dir):
    try:
        return json.loads((Path(out_dir) / 'nodo-context.json').read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        return {}


def _hdr(mode, target=None):
    return f'[nodo · {mode}{": " + target if target else ""}]\n'


def _issues_answer(ctx, file_filter=None, limit=15):
    issues = ctx.get('issues', [])
    if file_filter:
        issues = [i for i in issues if file_filter in i.get('file', '')]
    if not issues:
        return 'No issues found' + (f' in {file_filter}' if file_filter else '') + '.'
    conf = {'high': 0, 'medium': 1, 'low': 2}
    sev = {'error': 0, 'warn': 1, 'info': 2}
    issues = sorted(issues, key=lambda i: (conf.get(i.get('confidence'), 1), sev.get(i['severity'], 3)))
    lines = [f'{len(issues)} issue(s)' + (f' in {file_filter}' if file_filter else '')
             + ' — highest-confidence first:']
    for i in issues[:limit]:
        ln = f':L{i["line"]}' if i.get('line') else ''
        lines.append(f'  [{i.get("confidence", "?")}/{i["severity"]}] {i["type"]} — '
                     f'{i.get("file", "")}{ln}')
    if len(issues) > limit:
        lines.append(f'  … +{len(issues) - limit} more (see nodo-context.json)')
    return '\n'.join(lines)


def _hubs_answer(ctx):
    hubs = ctx.get('hubs', [])
    if not hubs:
        return 'No load-bearing hubs detected.'
    lines = ['Architectural hubs — highest blast radius (changing these reaches the most code):']
    for h in hubs[:12]:
        lines.append(f'  {h["file"]} — {h["edges"]} connections')
    return '\n'.join(lines)


def _topics_answer(ctx):
    k = ctx.get('knowledge', {})
    topics, gods = k.get('topics', []), k.get('god_nodes', [])
    if not topics and not gods:
        return ('No doc/PDF topics yet (add docs, or run with --full for PDFs). '
                'For code structure, ask about hubs or a specific file.')
    lines = []
    if gods:
        lines.append('God-nodes (most-connected concepts): '
                     + ', '.join(g['concept'] for g in gods[:8]))
    if topics:
        lines.append('Topics (knowledge-graph communities):')
        for t in topics[:10]:
            lines.append(f'  • {t["name"]}: ' + ', '.join(t['concepts'][:6]))
    return '\n'.join(lines)


def _menu():
    return ("I can answer about this codebase — try:\n"
            "  • \"what breaks if I change <file>?\"      (blast radius + change impact)\n"
            "  • \"how does <A> connect to <B>?\"          (import-path trace)\n"
            "  • \"who uses <Symbol>?\"                     (definition + references)\n"
            "  • \"what issues/bugs are in <file>?\"        (findings, high-confidence first)\n"
            "  • \"what are the key files / hubs?\"         (load-bearing files)\n"
            "  • \"where is <concept>?\"                    (code + docs + PDFs)\n"
            "  • \"what are the topics / overview?\"        (knowledge graph)")


def answer(question, nodes, edges, file_texts, out_dir, docs=None):
    """Route a natural-language question to the right primitive and answer it.
    Every answer is prefixed with how it was interpreted, so it's never a black box."""
    ctx = _ctx(out_dir)
    idx = build_symbol_index(nodes, file_texts)
    files, syms = _resolve_files_and_symbols(question, nodes, set(idx))

    # 1) "what breaks if I change <file>" → blast radius + change impact
    if files and _IMPACT.search(question):
        return _hdr('blast radius', files[0]) + query_file(out_dir, files[0])

    # 2) "how does A connect to B" → import-path trace
    if _CONNECT.search(question):
        endpoints = files[:]
        for s in syms:
            d = idx.get(s, {}).get('defs')
            if d:
                endpoints.append(d[0][0])
        endpoints = list(dict.fromkeys(endpoints))
        if len(endpoints) >= 2:
            return _hdr('path', f'{endpoints[0]} → {endpoints[1]}') + \
                path_between(out_dir, endpoints[0], endpoints[1])

    # 3) issues / bugs / what-to-fix (optionally scoped to a named file)
    if _ISSUE.search(question):
        scope = files[0] if files else None
        return _hdr('issues', scope) + _issues_answer(ctx, scope)

    # 4) key files / hubs (when no specific file was named)
    if _HUB.search(question) and not files:
        return _hdr('hubs') + _hubs_answer(ctx)

    # 5) a file named → blast radius
    if files:
        return _hdr('blast radius', files[0]) + query_file(out_dir, files[0])

    # 6) a symbol named → definition + references
    if syms:
        from .symbols import query_symbol
        out = query_symbol(nodes, file_texts, syms[0])
        if out:
            return _hdr('symbol', syms[0]) + out

    # 7) topics / overview
    if _TOPIC.search(question):
        return _hdr('topics') + _topics_answer(ctx)

    # 8) concept search over code + docs + PDFs
    concept = ' '.join(w for w in re.findall(r'[A-Za-z][\w-]+', question)
                       if w.lower() not in _QWORDS).strip()
    if not concept:
        return _menu()
    res = explain_concept(out_dir, concept, file_texts=file_texts, docs=docs)
    if 'No files clearly relate' in res:
        return res + '\n\n' + _menu()
    return _hdr('concept', concept) + res
