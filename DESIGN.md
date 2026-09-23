# ArchitectureGraph — Design

## Purpose

Architecture Graph gives coding agents an evidence-backed, queryable model of a repository before they change it. It separates stable facts from inferred relationships: every edge carries `confidence`, `provenance`, `evidence`, and optional Git `commit`.

## Four linked layers

| Layer | Question answered | Main nodes | Main relationships |
| --- | --- | --- | --- |
| Requirement Graph | *Why should it exist and what work realizes it?* | `REQUIREMENT`, `TASK`, `MODULE`, `API`, `EVENT` | `IMPLEMENTS_REQUIREMENT`, `DEPENDS_ON` |
| Architecture Graph | *What code exists and depends on what?* | `MODULE`, `FILE`, `SYMBOL` | `CONTAINS`, `IMPORTS`, `CALLS`, `CO_CHANGES_WITH` |
| Contract Graph | *What crosses a boundary?* | `API`, `EVENT`, `SCHEMA`, `MODULE` | `EXPOSES_API`, `CONSUMES_API`, `PUBLISHES`, `SUBSCRIBES`, `USES_SCHEMA` |
| Flow Graph | *How do data and events travel?* | `SYMBOL`, `API`, `EVENT`, `SCHEMA` | `READS`, `WRITES`, `DATA_FLOWS_TO`, `EVENT_FLOWS_TO` |

Layers share node IDs rather than copying objects. A requirement can map to an API and module; an API can be exposed by a symbol and carry a schema; a data-flow path can therefore be traced back to an intent.

## Canonical graph record

```text
Node: id, kind, name, qualified_name, file_path, attrs
Edge: source, target, type, confidence, provenance, evidence, commit, attrs
```

The current MVP emits `ast`, `lexical`, `contract_adapter`, `git`, `requirement_match`, and `simulation` as `provenance` values. `evidence` is a short source location/snippet or source-document reference. Confidence is a `[0,1]` number, never a claim of certainty.

## Node and edge vocabulary

The MVP may emit node kinds `MODULE`, `FILE`, `SYMBOL`, `API`, `EVENT`, `SCHEMA`, `REQUIREMENT`, and `TASK` (parse-warning only). It emits `CONTAINS`, `IMPORTS`, `CALLS`, `READS`, `WRITES`, `DATA_FLOWS_TO`, `EVENT_FLOWS_TO`, `EXPOSES_API`, `CONSUMES_API`, `PUBLISHES`, `SUBSCRIBES`, `USES_SCHEMA`, `IMPLEMENTS_REQUIREMENT`, `DEPENDS_ON`, and `CO_CHANGES_WITH` when corresponding source, contract, requirement, simulation, or Git evidence is present. `DATA_FLOWS_TO` is a local read/write heuristic; `EVENT_FLOWS_TO` is emitted for recognized publish/subscribe calls. The Python AST fallback emits `CONSUMES_API` for any literal-route `receiver.get/post/put/patch/delete(...)` call outside a route decorator, including when that decorator is on a direct class method; route decorators emit `EXPOSES_API` from the decorated method symbol. It does not identify whether a non-decorator receiver is an HTTP client. In the JS/TS lexical fallback, recognizable client receivers (including `api`, `client`, `http`, and `axios`) are consumers, unless the same receiver is first resolved as an explicit Router/express host. Recognizable route hosts (`app`, `router`, or `server`), names explicitly assigned from `Router(...)` or `express.Router(...)`/`express(...)` (including TypeScript annotations), and imports matching an exported Router/express alias are emitters of `EXPOSES_API`; other ambiguous receivers emit neither relationship. `simulate_change.kind` is restricted to this documented node-kind vocabulary, and inferred targets exclude the new hypothetical node so no simulation self-loop is created. These are evidence-backed heuristics, not interprocedural proofs.

### Emission inventory

This vocabulary is intentionally limited to values with a concrete emission point in the shipped source. `Indexer._file_module`, `LanguageAdapter.symbol`, and the adapters emit `FILE`, `MODULE`, `SYMBOL`, `API`, `EVENT`, and `SCHEMA`; `PythonAdapter.parse` emits `TASK` on a parse error; `AnalysisService.requirement_impact` and `simulate_change` emit `REQUIREMENT` and hypothetical `MODULE`/`API`/`EVENT` nodes. `indexer.py` emits `CONTAINS`, `IMPORTS`, `CALLS`, `READS`, `WRITES`, `DATA_FLOWS_TO`, `EVENT_FLOWS_TO`, `EXPOSES_API`, `CONSUMES_API`, `PUBLISHES`, `SUBSCRIBES`, and `USES_SCHEMA`; `service.py` emits `IMPLEMENTS_REQUIREMENT`, `DEPENDS_ON`, and `CO_CHANGES_WITH`. Their actual provenance values are `ast`, `lexical`, `contract_adapter`, `requirement_match`, `simulation`, and `git` respectively. No other node, edge, or provenance type is part of the documented MVP vocabulary.

## Analysis pipeline

1. Discover supported source and contract files.
2. Create `FILE`, `MODULE`, and `SYMBOL` nodes. Python uses `ast`; JS/TS uses an optional TypeScript compiler adapter, then a conservative lexical fallback.
3. Resolve imports and local calls where the target is unambiguous.
4. Extract lightweight reads/writes and their local data-flow relation, HTTP routes/client consumption, and publish/subscribe event-flow conventions.
5. Run contract adapters for OpenAPI, AsyncAPI, GraphQL, and Protobuf files.
6. Optionally enrich the graph from Git diffs and co-change history.
7. Persist to SQLite; export snapshots for repeatable graph-diff queries.

## Tool contract

All tools return JSON with `nodes`, `edges`, `paths`, `warnings`, and/or `summary`. They never fabricate a resolved relationship when the source evidence is ambiguous. CLI query commands and MCP tool names share one schema and are: `project_map`, `module_contract`, `trace_flow`, `trace_data`, `trace_event`, `requirement_impact`, `symbol_impact`, `contract_impact`, `simulate_change`, `graph_diff`, `git_diff_impact`, and `verify_spec`.

## Extension seams

`LanguageAdapter` can replace the fallback parser with Tree-sitter or a language server. `ContractAdapter` handles OpenAPI, AsyncAPI, GraphQL, and Proto and is designed for additional formats. `Enricher`-style imports are deliberately isolated so Joern/CodeQL can add precise CFG/DFG paths and OpenTelemetry can add runtime service/event edges without changing the graph schema.

## MVP boundaries

The MVP prioritizes dependency/call/contract discovery. JavaScript/TypeScript resolution, framework route recognition, alias resolution, and data-flow are conservative heuristics. It does not claim interprocedural, path-sensitive flow precision; Joern or CodeQL is the intended upgrade for that capability.
