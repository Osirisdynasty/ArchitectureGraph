"""Minimal MCP stdio JSON-RPC server; no third-party SDK required for the MVP."""
from __future__ import annotations

import json
import os
import sys

from .service import AnalysisService

TOOL_DESCRIPTIONS = {
 'project_map':'Return an architecture/contract overview of the indexed project.',
 'module_contract':'Return module boundary APIs, events, schemas, and imports. Input: module.',
 'trace_flow':'Trace call, data, and event relations from a node. Input: term, optional depth.',
 'trace_data':'Trace reads, writes, schemas, and call relations. Input: term, optional depth.',
 'trace_event':'Trace publishers/subscribers and event paths. Input: term, optional depth.',
 'requirement_impact':'Map a requirement to candidate modules and contracts. Input: requirement, requirement_id.',
 'symbol_impact':'Find reverse dependencies for a symbol/API/event. Input: symbol, optional depth.',
 'contract_impact':'Find consumers affected by an API/event/schema. Input: contract, optional depth.',
 'simulate_change':'Create a hypothetical node and affected graph. Input: description, kind, targets.',
 'graph_diff':'Compare exported snapshots. Input: before, after paths.',
 'git_diff_impact':'Map Git changed files to graph impact. Input: repo, rev, cochange.',
 'verify_spec':'Check that requirements and named modules appear in the graph. Input: spec object.'}

TOOL_SCHEMAS = {
 'project_map': {'properties':{}},
 'module_contract': {'properties':{'module':{'type':'string','description':'Indexed module name.'},'depth':{'type':'integer','default':2,'minimum':1}},'required':['module']},
 'trace_flow': {'properties':{'term':{'type':'string','description':'Node name or identifier to trace.'},'depth':{'type':'integer','default':5,'minimum':1}},'required':['term']},
 'trace_data': {'properties':{'term':{'type':'string','description':'Node name or identifier to trace.'},'depth':{'type':'integer','default':5,'minimum':1}},'required':['term']},
 'trace_event': {'properties':{'term':{'type':'string','description':'Node name or identifier to trace.'},'depth':{'type':'integer','default':5,'minimum':1}},'required':['term']},
 'requirement_impact': {'properties':{'requirement':{'type':'string','description':'Requirement text.'},'requirement_id':{'type':'string','default':'REQ-new'}},'required':['requirement']},
 'symbol_impact': {'properties':{'symbol':{'type':'string','description':'Symbol, API, or event name.'},'depth':{'type':'integer','default':4,'minimum':1}},'required':['symbol']},
 'contract_impact': {'properties':{'contract':{'type':'string','description':'API, event, or schema name.'},'depth':{'type':'integer','default':4,'minimum':1}},'required':['contract']},
 'simulate_change': {'properties':{'description':{'type':'string','description':'Proposed change.'},'kind':{'type':'string','default':'MODULE'},'targets':{'type':'array','items':{'type':'string'}}},'required':['description']},
 'graph_diff': {'properties':{'before':{'type':'string','description':'Path to the earlier exported snapshot.'},'after':{'type':'string','description':'Path to the later exported snapshot.'}},'required':['before','after']},
 'git_diff_impact': {'properties':{'repo':{'type':'string','description':'Git repository path.'},'rev':{'type':'string','default':'HEAD~1..HEAD'},'cochange':{'type':'boolean','default':False}},'required':['repo']},
 'verify_spec': {'properties':{'spec':{'type':'object','description':'Requirement specification object.'}},'required':['spec']},
}

def tool_defs():
    return [{'name':name,'description':desc,'inputSchema':{'type':'object',**TOOL_SCHEMAS[name],'additionalProperties':False}} for name,desc in TOOL_DESCRIPTIONS.items()]

def _checked_args(name:str,args:dict) -> dict:
    if name not in TOOL_SCHEMAS: raise ValueError(f'Unknown tool: {name}')
    if not isinstance(args,dict): raise ValueError(f'{name} arguments must be an object')
    schema=TOOL_SCHEMAS[name]; properties=schema['properties']; unknown=set(args)-set(properties)
    if unknown: raise ValueError(f"{name} does not accept argument(s): {', '.join(sorted(unknown))}")
    missing=[key for key in schema.get('required',[]) if key not in args]
    if missing: raise ValueError(f"{name} requires argument(s): {', '.join(missing)}")
    result={}
    for key,definition in properties.items():
        if key not in args:
            if 'default' in definition: result[key]=definition['default']
            continue
        value=args[key]; typ=definition['type']
        valid=(typ=='string' and isinstance(value,str)) or (typ=='integer' and isinstance(value,int) and not isinstance(value,bool)) or (typ=='boolean' and isinstance(value,bool)) or (typ=='array' and isinstance(value,list) and all(isinstance(x,str) for x in value)) or (typ=='object' and isinstance(value,dict))
        if not valid: raise ValueError(f"{name} argument '{key}' must be {typ}")
        if typ=='integer' and value < definition.get('minimum',value): raise ValueError(f"{name} argument '{key}' must be at least {definition['minimum']}")
        result[key]=value
    return result

def call(svc:AnalysisService,name:str,args:dict):
    args=_checked_args(name,args)
    if name=='project_map':return svc.project_map()
    if name=='module_contract':return svc.module_contract(args['module'],args['depth'])
    if name in {'trace_flow','trace_data','trace_event'}:return getattr(svc,name)(args['term'],args['depth'])
    if name=='symbol_impact':return svc.symbol_impact(args['symbol'],args['depth'])
    if name=='contract_impact':return svc.contract_impact(args['contract'],args['depth'])
    if name=='requirement_impact':return svc.requirement_impact(args['requirement'],args['requirement_id'])
    if name=='simulate_change':return svc.simulate_change(args['description'],args['kind'],args.get('targets'))
    if name=='graph_diff':return svc.graph_diff(args['before'],args['after'])
    if name=='git_diff_impact':return svc.git_diff_impact(args['repo'],args['rev'],args['cochange'])
    if name=='verify_spec':return svc.verify_spec(args['spec'])
    raise AssertionError(name)

def run(db:str):
    svc=AnalysisService(db)
    for line in sys.stdin:
        try:
            req=json.loads(line); method=req.get('method'); ident=req.get('id')
            if method=='initialize': result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'architecture-graph','version':'0.1.0'}}
            elif method=='tools/list': result={'tools':tool_defs()}
            elif method=='tools/call': result={'content':[{'type':'text','text':json.dumps(call(svc,req['params']['name'],req['params'].get('arguments',{})),ensure_ascii=False)}]}
            elif method.startswith('notifications/'): continue
            else: raise ValueError(f'Unsupported method: {method}')
            if ident is not None: print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
        except Exception as exc:
            if 'ident' in locals() and ident is not None: print(json.dumps({'jsonrpc':'2.0','id':ident,'error':{'code':-32000,'message':str(exc)}}),flush=True)
    svc.close()

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--db',default=os.environ.get('ARCHGRAPH_DB','architecture-graph.db'),help='SQLite graph database path (default: architecture-graph.db)');run(p.parse_args().db)
if __name__=='__main__':main()
