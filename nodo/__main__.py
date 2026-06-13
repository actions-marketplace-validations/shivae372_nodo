"""
Nodo CLI.

    python -m nodo [PATH] [options]

Scans a project, builds the dependency graph, detects issues, and writes the
interactive viewer + AI artifacts.
"""
import argparse
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__
from .scanner import build_graph
from .clustering import detect_communities, community_summaries
from .detectors import detect_all
from .config import load_config, write_sample_config
from .render import render


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='nodo',
        description='Map any codebase: dependency graph + issue detection + AI-agent artifacts. Zero dependencies.',
    )
    parser.add_argument('path', nargs='?', default='.',
                        help='Project root to scan (default: current directory)')
    parser.add_argument('-o', '--out', default=None,
                        help='Output directory (default: <path>/.nodo)')
    parser.add_argument('--name', default=None,
                        help='Project name shown in the viewer (default: folder name)')
    parser.add_argument('--open', action='store_true',
                        help='Open the generated HTML in your browser when done')
    parser.add_argument('--init', action='store_true',
                        help='Write a sample .nodo.json config file and exit')
    parser.add_argument('--no-gitignore', action='store_true',
                        help='Do not read .gitignore for extra ignore dirs')
    parser.add_argument('--ignore', action='append', default=[],
                        help='Extra directory name to ignore (repeatable)')
    parser.add_argument('--version', action='version', version=f'nodo {__version__}')
    args = parser.parse_args(argv)

    root = Path(args.path).resolve()
    if not root.is_dir():
        print(f'error: {root} is not a directory', file=sys.stderr)
        return 2

    if args.init:
        if write_sample_config(root):
            print(f'Wrote sample config: {root / ".nodo.json"}')
        else:
            print(f'Config already exists: {root / ".nodo.json"}')
        return 0

    cfg = load_config(root)
    project_name = args.name or cfg.get('project_name') or root.name
    out_dir = Path(args.out) if args.out else (root / '.nodo')

    ignore_dirs = list(cfg.get('ignore_dirs', [])) + list(args.ignore)
    # never scan our own output folder
    ignore_dirs.append(out_dir.name)

    t0 = time.time()
    print(f'nodo {__version__} — scanning {root} ...')
    nodes, edges, file_texts = build_graph(
        root,
        ignore_dirs=ignore_dirs,
        respect_gitignore=not args.no_gitignore,
        max_file_kb=cfg.get('max_file_kb', 512),
    )
    if not nodes:
        print('No source files found. Is this the right directory?', file=sys.stderr)
        return 1
    print(f'  {len(nodes)} files, {len(edges)} dependencies')

    communities = detect_communities(len(nodes), edges)
    comm_sum = community_summaries(communities, nodes)

    issues = detect_all(nodes, edges, file_texts, custom_rules=cfg.get('custom_rules'))
    n_e = sum(1 for i in issues if i['severity'] == 'error')
    n_w = sum(1 for i in issues if i['severity'] == 'warn')
    n_i = sum(1 for i in issues if i['severity'] == 'info')
    print(f'  {len(issues)} issues ({n_e} errors, {n_w} warnings, {n_i} info)')

    result = render(
        out_dir=out_dir,
        project_name=project_name,
        abs_root=str(root).replace('\\', '/'),
        nodes=nodes, edges=edges, communities=communities,
        comm_summaries=comm_sum, issues=issues,
        community_names=cfg.get('community_names'),
    )

    dt = time.time() - t0
    print(f'\nDone in {dt:.1f}s. Output in {out_dir}/')
    print(f'  - {Path(result["html"]).name:22} interactive viewer (open in a browser)')
    print(f'  - {Path(result["json"]).name:22} machine-readable graph + issues (for AI agents)')
    print(f'  - {Path(result["md"]).name:22} token-cheap summary')
    print(f'  - {Path(result["txt"]).name:22} plain-text issue list')

    if args.open:
        webbrowser.open(Path(result['html']).resolve().as_uri())

    return 0


if __name__ == '__main__':
    sys.exit(main())
