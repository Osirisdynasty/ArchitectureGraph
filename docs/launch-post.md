# Launch copy

These are ready-to-edit announcement drafts. Please update any personal wording before posting them under your own account.

## English

I built [ArchitectureGraph](https://github.com/Osirisdynasty/ArchitectureGraph), an open-source CLI and Codex MCP plugin for exploring a codebase as a local architecture graph.

It indexes Python and JavaScript/TypeScript code plus OpenAPI, AsyncAPI, GraphQL SDL, and Proto contracts into SQLite. You can trace an event, inspect a module's relationships, or get a candidate list of code affected by a requirement or proposed change—with source evidence and confidence attached.

The [two-minute example](https://github.com/Osirisdynasty/ArchitectureGraph#try-it-in-two-minutes) follows `order.created` from a Python publisher to a TypeScript subscriber. I also ran it against [Click's shell-completion module](https://github.com/Osirisdynasty/ArchitectureGraph/blob/main/docs/click-case-study.md) and documented the exact revision and source lines.

It's an MVP, not a full compiler or a runtime tracer. In particular, JS/TS resolution and impact estimates are heuristic, so they should guide source review rather than replace it. I'd especially value feedback on which architecture questions are most useful in everyday agent workflows.

MIT licensed. Repository: https://github.com/Osirisdynasty/ArchitectureGraph

## 中文

我做了一个开源工具 [ArchitectureGraph](https://github.com/Osirisdynasty/ArchitectureGraph)：把 Python、JavaScript/TypeScript 代码和部分 API/事件契约索引到本地 SQLite 架构图，再通过命令行或 Codex MCP 插件查询。

它适合回答几类问题：某个事件从哪里发出、在哪里被订阅？一个模块依赖什么？一项需求或拟议改动可能牵涉哪些代码？结果附源码证据和置信度，方便继续核对。

仓库里有一个[两分钟上手示例](https://github.com/Osirisdynasty/ArchitectureGraph#try-it-in-two-minutes)，展示订单事件如何串起 Python 发布者和 TypeScript 订阅者；还有一份基于真实开源项目 Click 的[可复现案例](https://github.com/Osirisdynasty/ArchitectureGraph/blob/main/docs/click-case-study.md)。

目前仍是 MVP：JS/TS 解析和改动影响评估含启发式推断，不能替代源码审查。很想听听大家在实际开发中最想让它回答什么架构问题。MIT 开源，欢迎试用和提 issue。
