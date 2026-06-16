"""
Auto-drafted lessons: induce candidate def/import regexes from real sample files.

When --self-check finds a language nodo can't parse, this proposes a *grounded*
lesson instead of a blank stub — it scans the actual files, recognises the
declaration/import shapes present (from a curated cross-language keyword library),
and emits only patterns that actually match the samples. Fully deterministic:
no ML, no LLM, no network — a principled heuristic that produces a DRAFT a human
or Claude then confirms. nodo proposes structurally-grounded evidence; the
intelligence on top verifies it. Bad guesses can't sneak in: every emitted regex
is validated, and the draft reports exactly how many symbols/imports it would
extract from your files so you can sanity-check before trusting it.
"""
import re

# Leading-keyword-then-identifier definition shapes, across many languages.
# (zig/rust: fn, go/swift: func, kotlin: fun, py/ruby/scala: def, perl: sub, …)
_DEF_KEYWORDS = [
    'fn', 'func', 'function', 'fun', 'def', 'proc', 'sub', 'method', 'macro',
    'class', 'struct', 'type', 'interface', 'trait', 'impl', 'enum', 'union',
    'module', 'mod', 'package', 'namespace', 'contract', 'object', 'record',
    'protocol', 'extension', 'actor', 'data', 'service', 'message', 'rpc',
]
# Binding shapes: keyword + name + (: | =)  — const/let/var/val declarations.
_BIND_KEYWORDS = ['const', 'let', 'var', 'val', 'local', 'define', 'set']

_IDENT = r'[A-Za-z_]\w*'


def _induce_defs(text):
    """Return (def_patterns, matched_keywords). Line-anchored, modifier-tolerant,
    each with exactly one capture group around the symbol name."""
    matched = []
    for kw in _DEF_KEYWORDS:
        rx = re.compile(r'(?m)^\s*(?:[\w@]+\s+){0,3}' + kw + r'\s+' + _IDENT)
        n = len(rx.findall(text))
        if n:
            matched.append((kw, n))
    pats = []
    if matched:
        kws = sorted({kw for kw, _ in matched})
        pats.append(r'(?m)^\s*(?:[\w@]+\s+){0,3}(?:' + '|'.join(kws) + r')\s+(' + _IDENT + r')')
    bind = []
    for kw in _BIND_KEYWORDS:
        rx = re.compile(r'(?m)^\s*(?:[\w@]+\s+){0,2}' + kw + r'\s+' + _IDENT + r'\s*[:=]')
        if rx.search(text):
            bind.append(kw)
    if bind:
        pats.append(r'(?m)^\s*(?:[\w@]+\s+){0,2}(?:' + '|'.join(sorted(bind))
                    + r')\s+(' + _IDENT + r')\s*[:=]')
    return pats, [k for k, _ in matched] + bind


# Import shapes, highest-precision first. (detector regex == emitted regex.)
_IMPORT_SHAPES = [
    ('quoted', r'(?m)^\s*(?:import|from|use|require|include|load|open|source|using|mod)\s+["\']([^"\'\n]+)["\']'),
    ('at_import', r'@import\(\s*["\']([^"\']+)["\']\s*\)'),
    ('c_include', r'(?m)^\s*#\s*include\s+[<"]([^>"\n]+)[>"]'),
    ('py_from', r'(?m)^\s*from\s+([\w.]+)\s+import\b'),
    ('bare', r'(?m)^\s*(?:import|use|require|using|open|load|include)\s+([A-Za-z_][\w./:@-]*)'),
]


def _induce_imports(text):
    """Return import_patterns that actually match the samples (deduped: the bare
    form is dropped when a quoted/from form already matched, to avoid noise)."""
    hits = {}
    for name, pat in _IMPORT_SHAPES:
        if re.compile(pat).search(text):
            hits[name] = pat
    if ('quoted' in hits or 'py_from' in hits) and 'bare' in hits:
        del hits['bare']
    # preserve _IMPORT_SHAPES order for determinism
    return [pat for name, pat in _IMPORT_SHAPES if name in hits]


def draft_lesson(ext, sample_texts, lang_name=None):
    """Induce a ready-to-`--teach` lesson for `ext` from its sample files.

    Returns (lesson_dict, stats). `stats` reports how many definitions/imports the
    draft would extract across the samples, so the draft is self-evidencing. Falls
    back to a fill-in stub if nothing could be induced (still valid to complete)."""
    name = (lang_name or ext.lstrip('.') or 'lang').lower()
    texts = [t for t in sample_texts if t]
    blob = '\n'.join(texts)[:400000]

    def_pats, def_kw = _induce_defs(blob)
    imp_pats = _induce_imports(blob)

    induced = bool(def_pats or imp_pats)
    spec = {
        'extensions': [ext],
        'category': 'lib',
        'def_patterns': def_pats or ['<regex with ONE capture group around the symbol name>'],
        'import_patterns': imp_pats or ['<regex with ONE capture group around the import target>'],
        'taught_by': 'nodo-draft' if induced else 'claude',
    }
    lesson = {'languages': {name: spec}}

    stats = {'defs': 0, 'imports': 0, 'samples': len(texts), 'induced': induced,
             'keywords': sorted(set(def_kw))}
    if induced:
        # dry-run the draft against the very files it came from — grounds trust.
        from . import lessons as _l
        fake_rel = 'sample' + ext
        names = []
        for t in texts:
            for nm, _ln in (_l.extract_defs(fake_rel, t, lesson) or []):
                names.append(nm)
            stats['imports'] += len(_l.extract_imports(fake_rel, t, lesson) or [])
        stats['defs'] = len(names)
        examples = sorted(set(names))[:6]
        spec['note'] = (f"Auto-drafted by nodo from {len(texts)} sample file(s): "
                        f"def keyword(s) {', '.join(sorted(set(def_kw))) or 'none'}; "
                        f"would extract {stats['defs']} definition(s)"
                        + (f" (e.g. {', '.join(examples)})" if examples else '')
                        + f" and {stats['imports']} import(s). VERIFY before trusting.")
    else:
        spec['note'] = (f"nodo could not induce patterns for {ext} automatically — "
                        f"fill the regexes (one capture group each) from the sample files.")
    return lesson, stats
