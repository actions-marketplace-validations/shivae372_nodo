"""
Zero-dependency BM25 search over the codebase, with a code-aware tokenizer and a
small concept-synonym map for vocabulary mismatch.

Lexical, not semantic — so it's transparent, fast, and needs no model. The
tokenizer splits camelCase / snake_case / kebab-case so `getUserToken` indexes as
`get user token`, and IDF down-weights ubiquitous terms (the common-word noise
problem). The synonym map bridges the obvious code-concept gaps (auth <-> login).
"""
import math
import re
from collections import Counter, defaultdict

# Structural words and language keywords that carry no search signal in code.
STOP = {
    'the', 'a', 'an', 'and', 'or', 'but', 'if', 'else', 'for', 'while', 'do',
    'return', 'const', 'let', 'var', 'function', 'class', 'import', 'export',
    'from', 'default', 'new', 'this', 'self', 'def', 'is', 'in', 'of', 'to',
    'as', 'be', 'on', 'at', 'by', 'it', 'with', 'true', 'false', 'null', 'none',
    'type', 'interface', 'public', 'private', 'static', 'async', 'await',
    'string', 'number', 'boolean', 'void', 'int', 'str', 'list', 'dict',
    # common English filler — keeps it out of concepts/topics/god-nodes
    'that', 'these', 'those', 'your', 'you', 'our', 'their', 'its', 'has',
    'have', 'had', 'was', 'were', 'will', 'would', 'should', 'can', 'could',
    'may', 'might', 'must', 'not', 'what', 'when', 'where', 'which', 'who',
    'how', 'all', 'any', 'each', 'more', 'most', 'some', 'such', 'only', 'than',
    'then', 'here', 'there', 'about', 'into', 'over', 'after', 'before', 'also',
    'just', 'via', 'we', 'they', 'them', 'i', 'so', 'no', 'yes', 'out', 'up',
    'one', 'two', 'per', 'add', 'see', 'using', 'used', 'make', 'made',
}

# Concept synonyms — bridges "auth" vs "login"/"jwt". Querying any term pulls in
# its siblings (lower-weighted). Generic across web/app codebases.
SYNONYMS = {
    'auth': ['login', 'logout', 'session', 'jwt', 'token', 'oauth', 'signin',
             'signup', 'credential', 'authenticate', 'authorize', 'guard'],
    'authentication': ['auth', 'login', 'session', 'jwt', 'oauth', 'credential'],
    'authorization': ['auth', 'permission', 'role', 'rbac', 'acl', 'guard'],
    'db': ['database', 'query', 'sql', 'orm', 'prisma', 'supabase', 'mongo',
           'table', 'schema', 'migration', 'repository'],
    'database': ['db', 'query', 'sql', 'orm', 'table', 'schema', 'migration'],
    'payment': ['billing', 'checkout', 'invoice', 'subscription', 'stripe',
                'charge', 'price', 'plan'],
    'billing': ['payment', 'checkout', 'invoice', 'subscription', 'plan', 'price'],
    'email': ['mail', 'smtp', 'resend', 'sendgrid', 'nodemailer', 'notify',
              'message', 'template'],
    'cache': ['redis', 'memo', 'memoize', 'store', 'ttl'],
    'config': ['settings', 'env', 'environment', 'options', 'dotenv'],
    'api': ['endpoint', 'route', 'handler', 'controller', 'rest', 'graphql'],
    'ui': ['component', 'view', 'page', 'screen', 'render', 'widget'],
    'test': ['spec', 'mock', 'fixture', 'assert', 'expect'],
    'error': ['exception', 'catch', 'throw', 'fail', 'crash', 'bug'],
    'upload': ['file', 'multipart', 'multer', 'attachment', 'storage'],
    'webhook': ['callback', 'event', 'signature', 'hook'],
}


def tokenize(text):
    """Split code text into lowercase tokens, breaking camelCase/snake/kebab."""
    # first split on non-alphanumeric
    rough = re.findall(r'[A-Za-z][A-Za-z0-9]*', text)
    out = []
    for w in rough:
        # split camelCase / PascalCase into parts
        parts = re.findall(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+', w)
        for p in (parts or [w]):
            p = p.lower()
            if len(p) > 1 and p not in STOP:
                out.append(p)
    return out


def expand_query(concept):
    """Lowercase + tokenize the query and add synonyms (weighted lower)."""
    base = tokenize(concept)
    weights = {t: 1.0 for t in base}
    for t in list(base):
        for syn in SYNONYMS.get(t, []):
            weights.setdefault(syn, 0.45)  # synonyms count, but less than direct hits
    return weights


class BM25:
    """Minimal BM25 over a set of documents. k1/b are standard defaults."""

    def __init__(self, docs, k1=1.5, b=0.75):
        # docs: list of (doc_id, token_list)
        self.k1, self.b = k1, b
        self.doc_ids = [d[0] for d in docs]
        self.tf = []                       # per-doc term frequency
        self.len = []                      # per-doc length
        df = defaultdict(int)              # document frequency per term
        for _id, toks in docs:
            c = Counter(toks)
            self.tf.append(c)
            self.len.append(len(toks))
            for term in c:
                df[term] += 1
        self.N = len(docs)
        self.avglen = (sum(self.len) / self.N) if self.N else 0
        # idf with the standard BM25 (+0.5 smoothing), floored at 0
        self.idf = {t: max(0.0, math.log((self.N - n + 0.5) / (n + 0.5) + 1))
                    for t, n in df.items()}

    def score(self, query_weights):
        """Return [(doc_id, score)] sorted desc for a {term: weight} query."""
        results = []
        for i in range(self.N):
            tf = self.tf[i]
            dl = self.len[i] or 1
            s = 0.0
            for term, qw in query_weights.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avglen or 1))
                s += qw * idf * (f * (self.k1 + 1)) / denom
            if s > 0:
                results.append((self.doc_ids[i], s))
        results.sort(key=lambda r: r[1], reverse=True)
        return results
