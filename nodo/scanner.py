"""
File discovery + dependency-graph construction.

Zero external dependencies. Resolves import/require statements across a project
into a node/edge graph. Each source file is a node; each resolved import is an
edge. Works language-agnostically with per-language import resolvers.
"""
import os
import re
from pathlib import Path

# Directories we never descend into.
DEFAULT_IGNORE_DIRS = {
    'node_modules', '.git', '.next', '.nuxt', 'dist', 'build', 'out',
    '__pycache__', '.venv', 'venv', 'env', '.turbo', '.cache', 'coverage',
    '.vercel', '.netlify', 'vendor', 'target', '.idea', '.vscode',
    '.svelte-kit', '.parcel-cache', 'bower_components', '.pytest_cache',
    '.mypy_cache', '.tox', 'site-packages', '.gradle', 'Pods', '.expo',
}

# Extensions we treat as source and try to parse imports from.
SOURCE_EXTS = {
    '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte',
    '.py', '.rb', '.go', '.rs', '.java', '.kt', '.php', '.cs', '.swift',
    '.c', '.h', '.cpp', '.hpp', '.cc', '.m', '.scala', '.dart', '.ex',
    '.exs', '.elm', '.sql',
}

# How files get categorized for colouring/grouping. Order matters: first match wins.
# Tuned to be useful across typical project layouts without assuming a framework.
CATEGORY_RULES = [
    ('test',      lambda p: bool(re.search(r'(\.test\.|\.spec\.|__tests__|/tests?/|_test\.)', p))),
    ('config',    lambda p: bool(re.search(r'(config|\.config\.|tsconfig|webpack|vite\.|rollup|babel|eslint|prettier)', p, re.I)) and '/src/' not in p),
    ('api',       lambda p: bool(re.search(r'(/api/|/routes?/|/controllers?/|/endpoints?/|/handlers?/|route\.(t|j)s)', p, re.I))),
    ('component', lambda p: bool(re.search(r'(/components?/|/ui/|/views?/|/widgets?/|\.vue$|\.svelte$)', p, re.I))),
    ('page',      lambda p: bool(re.search(r'(/pages?/|/screens?/|/app/.*page\.|/app/.*layout\.)', p, re.I))),
    ('store',     lambda p: bool(re.search(r'(/store/|/stores/|/state/|/redux/|/context/|/hooks?/)', p, re.I))),
    ('model',     lambda p: bool(re.search(r'(/models?/|/schema/|/entities/|/migrations?/|\.sql$)', p, re.I))),
    ('style',     lambda p: bool(re.search(r'\.(css|scss|sass|less|styl)$', p, re.I))),
    ('lib',       lambda p: bool(re.search(r'(/lib/|/libs/|/utils?/|/helpers?/|/services?/|/core/|/shared/|/common/)', p, re.I))),
]


def categorize(rel_path):
    p = rel_path.replace('\\', '/')
    for key, test in CATEGORY_RULES:
        try:
            if test(p):
                return key
        except re.error:
            continue
    return 'other'


def load_gitignore(root):
    """Best-effort parse of .gitignore into simple directory names to skip.
    Only plain directory entries (no globs) to stay dependency-free."""
    extra = set()
    gi = root / '.gitignore'
    if gi.exists():
        try:
            for line in gi.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('!'):
                    continue
                name = line.rstrip('/').lstrip('/')
                if name and '/' not in name and '*' not in name and name[0] != '.':
                    extra.add(name)
        except Exception:
            pass
    return extra


def discover_files(root, ignore_dirs, max_file_kb=512):
    """Yield (abs_path, rel_path) for every source file under root."""
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored dirs in-place so os.walk doesn't descend into them.
        # keep .github but drop other dotfolders and ignore-listed names.
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_dirs and (not d.startswith('.') or d == '.github')]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in SOURCE_EXTS:
                continue
            abs_path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(abs_path) > max_file_kb * 1024:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(abs_path, root).replace('\\', '/')
            yield abs_path, rel


# ── Import extraction per language family ────────────────────────────────────
JS_IMPORT_RES = [
    re.compile(r'''import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]'''),
    re.compile(r'''require\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''import\(\s*['"]([^'"]+)['"]\s*\)'''),
    re.compile(r'''export\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]'''),
]
PY_IMPORT_RES = [
    re.compile(r'^\s*from\s+([.\w]+)\s+import\b', re.M),
    re.compile(r'^\s*import\s+([.\w]+)', re.M),
]
GENERIC_IMPORT_RES = [
    re.compile(r'''(?:import|use|include|require|from)\s+['"<]([^'">\s]+)['">]'''),
]


def extract_imports(rel_path, text):
    """Return raw import target strings found in a file's text."""
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte'):
        regexes = JS_IMPORT_RES
    elif ext == '.py':
        regexes = PY_IMPORT_RES
    else:
        regexes = GENERIC_IMPORT_RES
    out = []
    for rx in regexes:
        out.extend(rx.findall(text))
    return out


# ── Resolving import strings to actual files in the project ──────────────────
def _build_resolution_index(rel_paths):
    """Index files by path-without-extension and by basename so imports resolve."""
    by_noext = {}      # 'src/lib/foo' -> rel_path
    by_basename = {}   # 'foo' -> [rel_paths]
    for rp in rel_paths:
        noext = re.sub(r'\.[^./]+$', '', rp)
        by_noext[noext] = rp
        if noext.endswith('/index'):
            by_noext[noext[:-len('/index')]] = rp
        if noext.endswith('/__init__'):
            by_noext[noext[:-len('/__init__')]] = rp
        base = noext.split('/')[-1]
        by_basename.setdefault(base, []).append(rp)
    return by_noext, by_basename


def _match_candidate(cand, by_noext):
    cand = cand.strip('/')
    if cand in by_noext:
        return by_noext[cand]
    for suffix in ('/index', '/__init__'):
        if cand + suffix in by_noext:
            return by_noext[cand + suffix]
    return None


def resolve_import(importer_rel, target, by_noext, by_basename):
    """Resolve one import string to a project-relative file path, or None if external."""
    target = target.strip()
    if not target:
        return None

    # Relative imports (JS ./ ../ or Python .mod) — resolve against importer dir.
    if target.startswith('.'):
        importer_dir = os.path.dirname(importer_rel)
        if re.match(r'^\.+/', target) or target in ('.', '..'):
            cand = os.path.normpath(os.path.join(importer_dir, target)).replace('\\', '/')
        else:
            # Python-style relative: leading dots = parent levels
            dots = len(target) - len(target.lstrip('.'))
            mod = target.lstrip('.').replace('.', '/')
            up = importer_dir
            for _ in range(max(0, dots - 1)):
                up = os.path.dirname(up)
            cand = os.path.normpath(os.path.join(up, mod)).replace('\\', '/')
        return _match_candidate(cand, by_noext)

    # Bare specifier: package alias, tsconfig path, python absolute, or external dep.
    aliased = re.sub(r'^@[/]?', '', target)   # '@/lib/x' -> 'lib/x'
    aliased = re.sub(r'^~/', '', aliased)     # '~/lib/x' -> 'lib/x'
    py_path = target.replace('.', '/')        # 'app.lib.x' -> 'app/lib/x'

    for cand in (target, aliased, py_path):
        hit = _match_candidate(cand, by_noext)
        if hit:
            return hit
        for prefix in ('src/', 'app/', 'lib/', 'packages/'):
            hit = _match_candidate(prefix + cand, by_noext)
            if hit:
                return hit

    # last resort: unique basename match
    base = target.replace('.', '/').split('/')[-1]
    cands = by_basename.get(base)
    if cands and len(cands) == 1:
        return cands[0]
    return None


def build_graph(root, ignore_dirs=None, respect_gitignore=True, max_file_kb=512):
    """Scan `root` and return (nodes, edges, file_texts).

    nodes:      list of {id, label, rel, category, loc}
    edges:      list of {source, target}  (file-id -> file-id)
    file_texts: {rel: text}  (cached so detectors don't re-read)
    """
    root = Path(root).resolve()
    ignore = set(DEFAULT_IGNORE_DIRS)
    if ignore_dirs:
        ignore |= set(ignore_dirs)
    if respect_gitignore:
        ignore |= load_gitignore(root)

    files = list(discover_files(root, ignore, max_file_kb))
    rel_paths = [rel for _, rel in files]
    by_noext, by_basename = _build_resolution_index(rel_paths)

    file_texts = {}
    raw_imports = {}
    for abs_path, rel in files:
        try:
            text = Path(abs_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            text = ''
        file_texts[rel] = text
        raw_imports[rel] = extract_imports(rel, text)

    id_of = {rel: i for i, rel in enumerate(rel_paths)}
    nodes = []
    for rel in rel_paths:
        loc = file_texts[rel].count('\n') + 1 if file_texts[rel] else 0
        nodes.append({
            'id': id_of[rel],
            'label': rel.split('/')[-1],
            'rel': rel,
            'category': categorize(rel),
            'loc': loc,
        })

    seen = set()
    edges = []
    for rel in rel_paths:
        src_id = id_of[rel]
        for target in raw_imports[rel]:
            resolved = resolve_import(rel, target, by_noext, by_basename)
            if resolved and resolved != rel:
                key = (src_id, id_of[resolved])
                if key not in seen:
                    seen.add(key)
                    edges.append({'source': src_id, 'target': id_of[resolved]})

    return nodes, edges, file_texts
