# Case study: mapping Click's shell-completion module

This is a reproducible smoke test against the public [Click](https://github.com/pallets/click) repository, at commit [`3cbaa76`](https://github.com/pallets/click/commit/3cbaa76). It demonstrates what ArchitectureGraph can show today, not a claim of complete static analysis.

```bash
git clone https://github.com/pallets/click.git
cd click
git checkout 3cbaa76
cd ..
archgraph index click/src/click --db click.db
archgraph module-contract shell_completion --depth 1 --db click.db
```

On this revision, indexing `src/click` found 17 Python source files, 2,466 graph nodes, and 20,536 edges. The `shell_completion` module query identifies `shell_completion.py` and its symbols, including `shell_complete` and `ShellComplete.get_completions`. It also reports source-backed imports from `shell_completion` to `core` at lines 9–15 and to `utils` at line 16. These relationships can be checked directly in [Click's source](https://github.com/pallets/click/blob/3cbaa76/src/click/shell_completion.py#L9-L16).

The useful question is: *If I change completion behavior, where should I start reading?* The module map points to the completion entry point and its local dependencies, with file-line evidence. It does **not** prove that every runtime path or downstream consumer has been found. This MVP's Python AST analysis is strongest for explicit imports and definitions; its data-flow and call edges can be noisy, and should be treated as leads for source review rather than authoritative impact claims.
