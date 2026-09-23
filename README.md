# ArchitectureGraph

**ArchitectureGraph** is a local Codex plugin and command-line tool for turning a Python or JavaScript/TypeScript repository into a queryable SQLite architecture graph. It helps coding agents answer the questions that are usually expensive to reconstruct from source code: what exists, what depends on it, which APIs and events form a flow, and what a proposed change is likely to affect.

It is designed for agent workflows where a small, evidence-backed map of the codebase is more useful than a raw file search.

## What it can do

- Build a graph of modules, files, symbols, APIs, events, schemas, requirements, and tasks.
- Trace dependency, data, and event flows across indexed Python and JS/TS code.
- Read OpenAPI, AsyncAPI, GraphQL SDL, and Proto descriptions into the contract graph.
- Estimate symbol, contract, requirement, Git diff, and hypothetical-change impact.
- Expose the same analysis surface through both CLI commands and a JSON-RPC MCP server.

> This is an MVP. Python analysis uses the native AST; JavaScript/TypeScript analysis intentionally uses conservative heuristics until a Tree-sitter or TypeScript-service enricher is added.

## Quick start

```powershell
cd ArchitectureGraph
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
archgraph index .\examples\commerce --db .\commerce.db
archgraph project-map --db .\commerce.db
archgraph trace-event order.created --db .\commerce.db
archgraph requirement-impact "notify a customer when an order is placed" --db .\commerce.db
archgraph simulate-change --db .\commerce.db --description "add SMS order notification" --kind EVENT
```

Use `archgraph --help` to see every command. `archgraph serve --db <path>` starts the MCP stdio server. After installing this plugin in Codex, its configured MCP server is `architecture-graph` and exposes the same query surface. The declared zero-argument `archgraph-mcp` entry point uses `ARCHGRAPH_DB` when set, otherwise creates/opens `architecture-graph.db` in its working directory; pass `--db <path>` for an explicit database.

## Tool examples

```powershell
archgraph module-contract src.orders --db .\commerce.db
archgraph symbol-impact src.orders.create_order --db .\commerce.db
archgraph git-diff-impact --db .\commerce.db --repo .\examples\commerce --rev HEAD~1..HEAD
archgraph snapshot --db .\commerce.db --out before.json
archgraph index .\examples\commerce --db .\commerce.db
archgraph snapshot --db .\commerce.db --out after.json
archgraph graph-diff --before before.json --after after.json
archgraph verify-spec --db .\commerce.db --spec .\examples\commerce\spec.json
```

`graph-diff` compares two snapshots directly and does not require `--db`. `verify-spec` accepts `{"requirements":[{"id":"REQ-1","text":"...","must_touch":["module.name"]}]}` and is independently runnable: it checks that every declared `must_touch` module exists and that the indexed graph contains at least one fact matching a meaningful requirement term. It does not require a prior `requirement-impact` call. Contract source files supported by the built-in adapters are OpenAPI/AsyncAPI JSON, GraphQL SDL, and Proto. YAML API descriptions are reported as adapter candidates unless converted to JSON; installing a YAML-capable adapter is intentionally deferred.

## What is implemented

- SQLite node/edge persistence with edge evidence, provenance, confidence, and commit fields.
- Python AST indexer; dependency/call graph; lightweight read/write and event heuristics.
- JavaScript/TypeScript import/declaration/call/event fallback parser; optional TypeScript adapter seam.
- Contract adapter interfaces plus OpenAPI, AsyncAPI, GraphQL, and Proto MVP adapters.
- Requirement candidate mapping, hypothetical changes, snapshot comparison, Git diff and co-change enrichment.
- CLI, JSON-RPC MCP server, example repository, and unit tests.

## Known limitations

- JavaScript/TypeScript route-host versus HTTP-client receiver classification is lexical and heuristic: it depends on receiver names and assignments in the same file. The design chooses **may miss, must not misclassify** for ambiguous forms, so only direct relative named imports of an explicitly exported Router alias are treated as cross-file route hosts. Cross-file same-name collisions, renamed imports, re-export chains, dynamic construction, package aliases, and default/namespace imports are unreliable and may be missed. Precise handling requires Tree-sitter or a TypeScript type service, neither of which is implemented.

## Not yet implemented

- Full TypeScript compiler or Tree-sitter resolution, package alias resolution, and framework-specific routing.
- Precise interprocedural/control-flow/taint analysis; use a future Joern or CodeQL enricher.
- Runtime correlation; a future OpenTelemetry enricher can ingest traces with `provenance=runtime`.
- YAML parsing, semantic requirement extraction, auth-aware API discovery, and a visual graph UI.

See [DESIGN.md](DESIGN.md) for the four-layer model and extension architecture.
