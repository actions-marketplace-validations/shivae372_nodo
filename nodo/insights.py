"""
Derived insights — auto-generated "Flows" and "Sensitive surfaces" analyses.

This is the brain-merge: graphify-style hand-written Data Flow / Security tabs,
but generated from the dependency graph so they work on ANY project with zero
configuration. No framework assumptions beyond generic patterns.

  - entry_flows():   pick entry points (API routes, pages, CLI mains) and trace
                     a few levels of imports inward — "this endpoint touches X, Y, Z".
  - sensitive_map(): classify files that handle auth, crypto, secrets, payments,
                     database, or external network calls into security layers.
"""
import re
from collections import defaultdict


# ── Entry-point detection ─────────────────────────────────────────────────────
def _is_entry(node):
    rel = node['rel'].lower()
    cat = node['category']
    base = rel.split('/')[-1]
    if cat in ('api', 'page'):
        return True
    if base in ('main.py', '__main__.py', 'index.js', 'index.ts', 'server.js',
                'server.ts', 'app.py', 'cli.py', 'manage.py'):
        return True
    if re.search(r'(route|handler|endpoint|controller)\.(t|j)s$', rel):
        return True
    return False


def entry_flows(nodes, edges, limit=20, depth=2):
    """For each entry point, return what it reaches via imports (depth-limited).

    Returns [{entry, category, reaches: [rel,...], reach_count}], sorted by reach.
    """
    id_to = {n['id']: n for n in nodes}
    out_adj = defaultdict(list)   # who do I import
    for e in edges:
        out_adj[e['source']].append(e['target'])

    entries = [n for n in nodes if _is_entry(n)]

    flows = []
    for ent in entries:
        seen = set()
        frontier = [ent['id']]
        for _ in range(depth):
            nxt = []
            for nid in frontier:
                for t in out_adj.get(nid, []):
                    if t not in seen and t != ent['id']:
                        seen.add(t)
                        nxt.append(t)
            frontier = nxt
        reaches = sorted(id_to[t]['rel'] for t in seen)
        if reaches:
            flows.append({
                'entry': ent['rel'],
                'category': ent['category'],
                'reaches': reaches,
                'reach_count': len(reaches),
            })
    # most-reaching entry points first — they're the most important paths
    flows.sort(key=lambda f: f['reach_count'], reverse=True)
    return flows[:limit]


# ── Sensitive-surface classification ─────────────────────────────────────────
# (layer, label, regex over path + content) — generic across languages/frameworks.
SENSITIVE_LAYERS = [
    ('auth', 'Authentication & Authorization',
     re.compile(r'\b(getUser\(|getSession\(|signIn|signOut|jwt\.|verifyToken|'
                r'authenticate|authorize|requireAuth|isAdmin|adminGuard|'
                r'hasPermission|checkRole|rbac|\.auth\.|passport\.|bcrypt\.compare|'
                r'oauth|login|logout)\b', re.I)),
    ('crypto', 'Cryptography & Hashing',
     re.compile(r'\b(crypto|encrypt|decrypt|hash|hmac|bcrypt|argon2|scrypt|'
                r'cipher|signature|sign\(|verify\(|randomBytes|secretbox)\b', re.I)),
    ('secrets', 'Secrets & Environment',
     re.compile(r'(process\.env|os\.environ|getenv|API_KEY|SECRET|PRIVATE_KEY|'
                r'\.env|dotenv|credentials)', re.I)),
    ('payment', 'Payments & Billing',
     re.compile(r'\b(stripe\.|paypal|dodopayments|\.charges\.|\.subscriptions\.|'
                r'createCheckout|createInvoice|payment_intent|webhookSecret|'
                r'priceId|productId)\b', re.I)),
    ('database', 'Database & Data Access',
     re.compile(r'(\.from\([\'"]|prisma\.|mongoose\.|sequelize|knex\(|'
                r'\.rpc\(|INSERT INTO|UPDATE .*SET|DELETE FROM|CREATE TABLE|'
                r'\.insert\(|\.update\(|\.delete\(|\.select\()', re.I)),
    ('network', 'External Network Calls',
     re.compile(r'\b(fetch\(|axios|http\.|https\.|requests\.|urllib|got\(|'
                r'webhook|api\.|\.post\(|\.get\()', re.I)),
    ('upload', 'File Upload & User Input',
     re.compile(r'\b(multer|upload|formidable|multipart|req\.body|req\.file|'
                r'sanitize|validate|dangerouslySetInnerHTML|eval\()', re.I)),
]


def sensitive_map(nodes, file_texts, per_layer=12):
    """Classify files into security-relevant layers by path + content patterns.

    Returns [{layer, label, files: [{rel, hits:[matched terms]}], count}].
    """
    layers = []
    for key, label, rx in SENSITIVE_LAYERS:
        matched = []
        for n in nodes:
            rel = n['rel']
            text = file_texts.get(rel, '')
            # match on path OR content; content match is stronger signal
            path_hit = rx.search(rel)
            body_hits = rx.findall(text) if text else []
            if path_hit or body_hits:
                terms = set()
                if path_hit:
                    terms.add(path_hit.group(0).lower())
                for h in body_hits[:5]:
                    t = (h if isinstance(h, str) else h[0]).strip().lower()
                    if t:
                        terms.add(t[:24])
                matched.append({'rel': rel, 'hits': sorted(terms)[:6],
                                'strength': len(body_hits) + (2 if path_hit else 0)})
        matched.sort(key=lambda m: m['strength'], reverse=True)
        if matched:
            layers.append({
                'layer': key, 'label': label,
                'files': [{'rel': m['rel'], 'hits': m['hits']} for m in matched[:per_layer]],
                'count': len(matched),
            })
    return layers
