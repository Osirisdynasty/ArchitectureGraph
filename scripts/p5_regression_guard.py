"""Executable P5 guard for the declared MCP process and the three public surfaces."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from archgraph.cli import TOOLS, build_parser
from archgraph.graph import GraphStore
from archgraph.indexer import Indexer
from archgraph.mcp import TOOL_SCHEMAS, tool_defs
from archgraph.service import AnalysisService


PROJECT = Path(__file__).resolve().parents[1]
VOCABULARY = (
    'MODULE','FILE','SYMBOL','API','EVENT','SCHEMA','REQUIREMENT','TASK',
    'CONTAINS','IMPORTS','CALLS','READS','WRITES','DATA_FLOWS_TO','EVENT_FLOWS_TO',
    'EXPOSES_API','CONSUMES_API','PUBLISHES','SUBSCRIBES','USES_SCHEMA',
    'IMPLEMENTS_REQUIREMENT','DEPENDS_ON','CO_CHANGES_WITH',
    'ast','lexical','contract_adapter','requirement_match','simulation','git',
)


def main() -> None:
    declaration = json.loads((PROJECT / '.mcp.json').read_text(encoding='utf-8'))['mcpServers']['architecture-graph']
    assert declaration == {'command':'archgraph-mcp','args':[],'env':{}}, declaration
    with tempfile.TemporaryDirectory(prefix='archgraph-p5-mcp-') as directory:
        executable = Path(sys.executable).with_name('archgraph-mcp.exe')
        frames = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
            {'jsonrpc':'2.0','method':'notifications/initialized','params':{}},
            {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'project_map','arguments':{}}},
        ]
        result = subprocess.run([str(executable)], cwd=directory, input=''.join(json.dumps(x)+'\n' for x in frames), text=True, capture_output=True)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        assert result.returncode == 0, result.stderr
        assert [line['id'] for line in lines] == [1,2,3], lines
        assert len(lines[1]['result']['tools']) == 12, lines[1]
        print(f'MCP_DECLARED_ZERO_ARG_EXIT={result.returncode} RESPONSE_IDS={[line["id"] for line in lines]} TOOLS={len(lines[1]["result"]["tools"])} STDERR={result.stderr!r}')

    schemas = {tool['name']:tool['inputSchema'] for tool in tool_defs()}
    assert set(TOOLS) == set(TOOL_SCHEMAS) == set(schemas) and len(schemas) == 12
    parser = build_parser()
    commands = next(action.choices for action in parser._actions if hasattr(action, 'choices') and action.choices)
    for name, schema in TOOL_SCHEMAS.items():
        service = getattr(AnalysisService, name)
        service_params = {p.name for p in inspect.signature(service).parameters.values() if p.name != 'self'}
        cli_params = {a.dest for a in commands[name.replace('_','-')]._actions if a.dest not in {'help','db'}}
        mcp_params = set(schema['properties'])
        assert service_params == cli_params == mcp_params, (name, service_params, cli_params, mcp_params)
    print(f'SIGNATURES_CLI_SERVICE_MCP_MATCH=12/12 TOOL_SET_CLI_MCP_MATCH=12/12')

    design = (PROJECT / 'DESIGN.md').read_text(encoding='utf-8')
    source = '\n'.join((PROJECT / 'src' / 'archgraph' / part).read_text(encoding='utf-8') for part in ('indexer.py','service.py'))
    for token in VOCABULARY:
        assert f'`{token}`' in design and repr(token) in source, token

    # Exercise every documented runtime vocabulary value rather than only
    # asserting that its spelling appears in an implementation branch.
    with tempfile.TemporaryDirectory(prefix='archgraph-p5-vocabulary-') as directory:
        root = Path(directory)
        (root / 'runtime.py').write_text(
            'from bus import publish, subscribe\nfrom helpers import run\n'
            '@app.get("/route")\ndef route(client):\n'
            '    value = source\n    client.post("/consumed")\n'
            '    publish("runtime.event")\n    subscribe("runtime.event")\n'
            '    return run(value)\n', encoding='utf-8')
        (root / 'broken.py').write_text('def broken(:\n', encoding='utf-8')
        (root / 'openapi.json').write_text(json.dumps({'openapi':'3.0.0','paths':{'/contract':{'get':{'responses':{'200':{'content':{'application/json':{'schema':{'$ref':'#/components/schemas/Item'}}}}}}}},'components':{'schemas':{'Item':{'type':'object'}}}}), encoding='utf-8')
        (root / 'asyncapi.json').write_text(json.dumps({'asyncapi':'2.0.0','channels':{'runtime.event':{'publish':{},'subscribe':{}}}}), encoding='utf-8')
        (root / 'schema.proto').write_text('message Request {} message Response {} service Runtime { rpc Call(Request) returns (Response); }', encoding='utf-8')
        (root / 'schema.graphql').write_text('type Query { status: String }', encoding='utf-8')
        (root / 'client.ts').write_text('function request(client) { return client.post("/lexical"); }', encoding='utf-8')
        (root / 'companion.py').write_text('def companion():\n    return None\n', encoding='utf-8')
        for args in (['init'], ['config','user.email','guard@example.invalid'], ['config','user.name','P5 Guard'], ['add','.'], ['commit','-m','initial']):
            subprocess.run(['git','-C',str(root),*args], check=True, capture_output=True, text=True)
        (root / 'runtime.py').write_text((root / 'runtime.py').read_text(encoding='utf-8') + '\n# second revision\n', encoding='utf-8')
        (root / 'companion.py').write_text((root / 'companion.py').read_text(encoding='utf-8') + '\n# second revision\n', encoding='utf-8')
        subprocess.run(['git','-C',str(root),'add','runtime.py','companion.py'], check=True, capture_output=True, text=True)
        subprocess.run(['git','-C',str(root),'commit','-m','cochange'], check=True, capture_output=True, text=True)
        db = root / 'graph.db'; graph = GraphStore(db); Indexer(graph,root).index(); graph.close()
        service = AnalysisService(db)
        service.requirement_impact('route runtime', 'REQ-runtime')
        service.simulate_change('runtime dependency', 'MODULE', ['MODULE:runtime'])
        service.git_diff_impact(str(root), 'HEAD~1..HEAD', cochange=True)
        exported = service.g.export(); service.close()
        observed_nodes = {node['kind'] for node in exported['nodes']}
        observed_edges = {edge['type'] for edge in exported['edges']}
        observed_provenance = {edge['provenance'] for edge in exported['edges']}
        expected_nodes = {'MODULE','FILE','SYMBOL','API','EVENT','SCHEMA','REQUIREMENT','TASK'}
        expected_edges = {'CONTAINS','IMPORTS','CALLS','READS','WRITES','DATA_FLOWS_TO','EVENT_FLOWS_TO','EXPOSES_API','CONSUMES_API','PUBLISHES','SUBSCRIBES','USES_SCHEMA','IMPLEMENTS_REQUIREMENT','DEPENDS_ON','CO_CHANGES_WITH'}
        expected_provenance = {'ast','lexical','contract_adapter','requirement_match','simulation','git'}
        assert observed_nodes == expected_nodes, observed_nodes
        assert observed_edges == expected_edges, observed_edges
        assert observed_provenance == expected_provenance, observed_provenance
        print(f'RUNTIME_NODE_KINDS={sorted(observed_nodes)}')
        print(f'RUNTIME_EDGE_TYPES={sorted(observed_edges)}')
        print(f'RUNTIME_PROVENANCE={sorted(observed_provenance)}')
    print(f'DOC_RUNTIME_VOCABULARY_BIDIRECTIONAL=OK TOKENS={len(VOCABULARY)}')


if __name__ == '__main__':
    main()
