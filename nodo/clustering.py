"""
Community detection — zero dependency.

Uses synchronous label propagation, which needs no external libraries and runs
in near-linear time. Good enough to surface module clusters; not as refined as
Louvain, but dependency-free and deterministic with a fixed seed.
"""
import random
from collections import defaultdict, Counter


def detect_communities(num_nodes, edges, iterations=30, seed=42):
    """Return a dict {node_id: community_id} via label propagation.

    Communities are renumbered by descending size so community 0 is the largest.
    """
    if num_nodes == 0:
        return {}

    adj = defaultdict(list)
    for e in edges:
        a, b = e['source'], e['target']
        adj[a].append(b)
        adj[b].append(a)

    # initialise each node with its own label
    labels = {i: i for i in range(num_nodes)}
    rng = random.Random(seed)
    order = list(range(num_nodes))

    for _ in range(iterations):
        rng.shuffle(order)
        changed = False
        for node in order:
            neighbours = adj.get(node)
            if not neighbours:
                continue
            counts = Counter(labels[n] for n in neighbours)
            top = max(counts.values())
            # tie-break deterministically by smallest label among the winners
            best = min(lbl for lbl, c in counts.items() if c == top)
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break

    # renumber communities by descending size
    sizes = Counter(labels.values())
    ordered = [lbl for lbl, _ in sizes.most_common()]
    remap = {lbl: new for new, lbl in enumerate(ordered)}
    return {node: remap[lbl] for node, lbl in labels.items()}


def community_summaries(communities, nodes, top_n=14):
    """Return [{id, size, sample, top_dir}] for the largest communities."""
    by_comm = defaultdict(list)
    id_to_node = {n['id']: n for n in nodes}
    for node_id, comm in communities.items():
        n = id_to_node.get(node_id)
        if n:
            by_comm[comm].append(n)

    out = []
    for comm, members in sorted(by_comm.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_n]:
        # infer a human label from the most common top-level directory
        dirs = Counter()
        for m in members:
            parts = m['rel'].split('/')
            dirs[parts[0] if len(parts) > 1 else '(root)'] += 1
        top_dir = dirs.most_common(1)[0][0] if dirs else '(root)'
        cats = Counter(m['category'] for m in members)
        cat_label = cats.most_common(1)[0][0]
        out.append({
            'id': comm,
            'size': len(members),
            'top_dir': top_dir,
            'category': cat_label,
            'name': f'{top_dir}/ — mostly {cat_label}',
            'sample': [m['label'] for m in members[:6]],
        })
    return out
