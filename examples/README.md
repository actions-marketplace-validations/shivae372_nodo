# Examples

Nodo needs no example project — point it at any real codebase:

```bash
python -m nodo /path/to/any/project --open
```

## Try it on Nodo itself

```bash
python -m nodo . --name Nodo --open
```

You'll see Nodo's own 8-file dependency graph: `__main__` fanning out to
`scanner`, `clustering`, `detectors`, `config`, and `render`, with `render`
pulling in `template`.

## Try it on a big project

Clone anything and run Nodo on it — a Next.js app, a Django project, a Go
service. The graph, modules, and issue list adapt automatically. Nothing is
configured per-framework.

## Custom rules

Drop a `.nodo.json` in the project root to add your own checks:

```bash
python -m nodo /path/to/project --init   # writes a starter .nodo.json
```

Then edit the `custom_rules` array — see the main README for the schema.
