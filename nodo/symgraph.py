"""
Symbol-level graph (advanced mode) — functions, classes and methods as
first-class nodes, with `defines` (file → symbol), `calls` (symbol → symbol) and
`inherits` (class → base) edges. Deterministic, tree-sitter only (JS/TS/Python).

This is the finer-grained layer: file-level imports tell you *which files* relate;
the symbol graph tells you *which functions/classes* relate, and how. Output is
both hierarchical (`by_file`: each file's symbols) and flat (`nodes`/`edges`) so an
agent can traverse either way. Names are resolved by identifier (classic
approximation) — fast, private, no LLM.
"""
import os
from collections import defaultdict

_JSTS = {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'}


def available():
    from . import callgraph
    return callgraph.available()


def _clean_doc(s):
    """First line of a docstring/comment, stripped of comment syntax and quotes."""
    s = s.strip().strip('"\'`')
    for pre in ('/**', '*/', '//', '#', '*'):
        s = s.strip().lstrip(pre)
    s = s.strip().strip('"\'`').strip()
    first = next((ln.strip(' *') for ln in s.splitlines() if ln.strip(' *')), '')
    return first[:120]


def _symbols(rel, text):
    """(symbols, inherits): symbols=[(name, kind, start, end, doc)] (kind func/
    class/method; doc = leading docstring/comment rationale or ''); inherits=
    [(class, base)] from extends/implements/superclasses."""
    from . import ast_index
    ext = os.path.splitext(rel)[1].lower()
    parser = ast_index._get_parser(ext)
    if parser is None:
        return [], []
    try:
        src = text.encode('utf-8')
        tree = parser.parse(src)
    except Exception:
        return [], []

    def txt(n):
        return src[n.start_byte:n.end_byte].decode('utf-8', 'ignore')

    def field(n, k):
        try:
            return n.child_by_field_name(k)
        except Exception:
            return None

    is_py = ext == '.py'

    def doc_of(n):
        # rationale: Python body docstring, or a JS leading comment/jsdoc
        if is_py:
            body = field(n, 'body')
            if body is not None:
                for c in body.children:
                    if c.is_named:
                        if c.type == 'expression_statement' and c.children and \
                                c.children[0].type == 'string':
                            return _clean_doc(txt(c.children[0]))
                        break
            return ''
        prev = n.prev_named_sibling if hasattr(n, 'prev_named_sibling') else None
        if prev is not None and prev.type == 'comment':
            return _clean_doc(txt(prev))
        return ''

    def add(syms, name, kind, n):
        syms.append((name, kind, n.start_point[0] + 1, n.end_point[0] + 1, doc_of(n)))

    syms, inh = [], []
    for n in ast_index._walk(tree.root_node):
        t = n.type
        if is_py:
            if t == 'function_definition':
                nm = field(n, 'name')
                if nm:
                    add(syms, txt(nm), 'func', n)
            elif t == 'class_definition':
                nm = field(n, 'name')
                if nm:
                    cname = txt(nm)
                    add(syms, cname, 'class', n)
                    sup = field(n, 'superclasses')
                    if sup:
                        for c in sup.children:
                            if c.type in ('identifier', 'attribute'):
                                inh.append((cname, txt(c).split('.')[-1]))
        else:
            if t in ('function_declaration', 'generator_function_declaration'):
                nm = field(n, 'name')
                if nm and nm.type == 'identifier':
                    add(syms, txt(nm), 'func', n)
            elif t == 'method_definition':
                nm = field(n, 'name')
                if nm and nm.type in ('property_identifier', 'identifier'):
                    add(syms, txt(nm), 'method', n)
            elif t == 'class_declaration':
                nm = field(n, 'name')
                if nm:
                    cname = txt(nm)
                    add(syms, cname, 'class', n)
                    for c in n.children:
                        if c.type == 'class_heritage':
                            for d in ast_index._walk(c):
                                if d.type in ('identifier', 'type_identifier'):
                                    base = txt(d)
                                    if base != cname:
                                        inh.append((cname, base))
            elif t == 'variable_declarator':
                nm, val = field(n, 'name'), field(n, 'value')
                if (nm and nm.type == 'identifier' and val is not None
                        and val.type in ('arrow_function', 'function_expression', 'function')):
                    add(syms, txt(nm), 'func', n)
    return syms, inh


def build_symbol_graph(nodes, file_texts, cap=8000):
    """Return {available, nodes, edges, by_file, counts}. Empty unless AST is active."""
    if not available():
        return {'available': False, 'nodes': [], 'edges': [], 'by_file': {}, 'counts': {}}
    from . import callgraph
    cg = callgraph.build_call_graph(nodes, file_texts, cap=cap)

    by_file, defined, classes = defaultdict(list), set(), set()
    inherits, contains = [], []
    for n in nodes:
        rel = n['rel']
        if os.path.splitext(rel)[1].lower() not in (_JSTS | {'.py'}):
            continue
        text = file_texts.get(rel, '')
        if not text:
            continue
        syms, inh = _symbols(rel, text)
        cranges = [(nm, s, e) for nm, kind, s, e, _d in syms if kind == 'class']
        seen_names = set()
        for nm, kind, start, end, doc in syms:
            if nm in seen_names:
                continue
            seen_names.add(nm)
            by_file[rel].append({'name': nm, 'kind': kind, 'line': start, 'doc': doc})
            defined.add(nm)
            if kind == 'class':
                classes.add(nm)
        for nm, kind, start, end, doc in syms:           # symbol → enclosing class (innermost)
            for cn, cs, ce in sorted(cranges, key=lambda r: r[2] - r[1]):
                if cs <= start <= ce and cn != nm:
                    contains.append((rel, cn, nm))
                    break
        inherits.extend((cls, base) for cls, base in inh)

    gnodes, gedges, sym_id = [], [], {}
    for rel in sorted(by_file):
        gnodes.append({'id': f'file:{rel}', 'label': rel.split('/')[-1], 'kind': 'file', 'rel': rel})
        for s in by_file[rel]:
            sid = f'sym:{rel}:{s["name"]}'
            sym_id.setdefault(s['name'], sid)        # name → first definition
            node = {'id': sid, 'label': s['name'], 'kind': 'symbol',
                    'symtype': s['kind'], 'rel': rel, 'line': s['line']}
            if s.get('doc'):
                node['rationale'] = s['doc']
            gnodes.append(node)
            gedges.append({'from': f'file:{rel}', 'to': sid, 'type': 'defines'})
    for rel, cn, meth in contains:                       # class → method containment
        gedges.append({'from': f'sym:{rel}:{cn}', 'to': f'sym:{rel}:{meth}', 'type': 'contains'})
    for e in cg.get('edges', []):
        a, b = sym_id.get(e['from']), sym_id.get(e['to'])
        if a and b and a != b:
            gedges.append({'from': a, 'to': b, 'type': 'calls'})
    for cls, base in inherits:
        a, b = sym_id.get(cls), sym_id.get(base)
        if a and b and base in classes and a != b:
            gedges.append({'from': a, 'to': b, 'type': 'inherits'})

    seen, ded = set(), []
    for e in gedges:
        k = (e['from'], e['to'], e['type'])
        if k in seen:
            continue
        seen.add(k)
        ded.append(e)
    ded = ded[:cap]
    counts = {'files': len(by_file),
              'symbols': sum(len(v) for v in by_file.values()),
              'classes': len(classes),
              'defines': sum(1 for e in ded if e['type'] == 'defines'),
              'calls': sum(1 for e in ded if e['type'] == 'calls'),
              'contains': sum(1 for e in ded if e['type'] == 'contains'),
              'inherits': sum(1 for e in ded if e['type'] == 'inherits')}
    return {'available': True, 'nodes': gnodes, 'edges': ded,
            'by_file': {k: by_file[k] for k in sorted(by_file)}, 'counts': counts}
