from __future__ import annotations

import argparse
import json
from pathlib import Path

from .graph import GraphStore
from .indexer import Indexer
from .mcp import TOOL_SCHEMAS
from .service import AnalysisService

# MCP is the public tool contract.  Keep the CLI command surface derived from
# that same contract so a tool cannot quietly exist on only one surface.
TOOLS = tuple(TOOL_SCHEMAS)

def output(value:dict): print(json.dumps(value,indent=2,ensure_ascii=False,default=str))
def add_db(p): p.add_argument('--db',required=True,help='SQLite graph database path')
def positive_int(value:str)->int:
    parsed=int(value)
    if parsed < 1: raise argparse.ArgumentTypeError('must be at least 1')
    return parsed

def build_parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog='archgraph',description='ArchitectureGraph')
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('index');q.add_argument('root');add_db(q);q.add_argument('--keep',action='store_true')
    q=sub.add_parser('snapshot');add_db(q);q.add_argument('--out',required=True)
    q=sub.add_parser('serve');add_db(q)
    for name in TOOLS:
        q=sub.add_parser(name.replace('_','-'))
        if name != 'graph_diff': add_db(q)
        if name=='module_contract':q.add_argument('module');q.add_argument('--depth',type=positive_int,default=2)
        elif name in {'trace_flow','trace_data','trace_event'}:q.add_argument('term');q.add_argument('--depth',type=positive_int,default=5)
        elif name=='symbol_impact':q.add_argument('symbol');q.add_argument('--depth',type=positive_int,default=4)
        elif name=='contract_impact':q.add_argument('contract');q.add_argument('--depth',type=positive_int,default=4)
        elif name=='requirement_impact':q.add_argument('requirement');q.add_argument('--requirement-id',default='REQ-new')
        elif name=='simulate_change':q.add_argument('--description',required=True);q.add_argument('--kind',default='MODULE');q.add_argument('--targets',nargs='*')
        elif name=='graph_diff':q.add_argument('--before',required=True);q.add_argument('--after',required=True)
        elif name=='git_diff_impact':q.add_argument('--repo',required=True);q.add_argument('--rev',default='HEAD~1..HEAD');q.add_argument('--cochange',action='store_true')
        elif name=='verify_spec':q.add_argument('--spec',required=True)
    return p

def main(argv=None):
    p=build_parser()
    a=p.parse_args(argv)
    if a.command=='index':
        g=GraphStore(a.db); result=Indexer(g,a.root).index(not a.keep);g.close();output(result);return
    if a.command=='snapshot':
        g=GraphStore(a.db);Path(a.out).write_text(json.dumps(g.export(),indent=2),encoding='utf-8');g.close();output({'snapshot':a.out});return
    if a.command=='serve':
        from .mcp import run; run(a.db);return
    name=a.command.replace('-','_')
    if name=='graph_diff':
        output(AnalysisService.graph_diff(a.before,a.after));return
    svc=AnalysisService(a.db)
    if name=='project_map':result=svc.project_map()
    elif name=='module_contract':result=svc.module_contract(a.module,a.depth)
    elif name in {'trace_flow','trace_data','trace_event'}:result=getattr(svc,name)(a.term,a.depth)
    elif name=='symbol_impact':result=svc.symbol_impact(a.symbol,a.depth)
    elif name=='contract_impact':result=svc.contract_impact(a.contract,a.depth)
    elif name=='requirement_impact':result=svc.requirement_impact(a.requirement,a.requirement_id)
    elif name=='simulate_change':result=svc.simulate_change(a.description,a.kind,a.targets)
    elif name=='git_diff_impact':result=svc.git_diff_impact(a.repo,a.rev,a.cochange)
    elif name=='verify_spec':result=svc.verify_spec(json.loads(Path(a.spec).read_text(encoding='utf-8')))
    else:raise AssertionError(name)
    svc.close();output(result)

if __name__=='__main__':main()
