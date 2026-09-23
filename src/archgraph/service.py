from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from .graph import GraphStore
from .model import Edge, Node

SIMULATION_KINDS={'MODULE','FILE','SYMBOL','API','EVENT','SCHEMA','REQUIREMENT','TASK'}


def pack(nodes: list[Node], edges: list[Edge], **extra: Any) -> dict[str, Any]:
    return {"nodes":[n.json() for n in nodes],"edges":[e.json() for e in edges],**extra}

class AnalysisService:
    def __init__(self, db: str | Path): self.g=GraphStore(db)
    def close(self): self.g.close()
    def project_map(self)->dict:
        nodes=[n for n in self.g.export()['nodes'] if n['kind'] in {'MODULE','FILE','API','EVENT','SCHEMA'}]
        raw=self.g.export()['edges']; edges=[e for e in raw if e['type'] in {'IMPORTS','EXPOSES_API','PUBLISHES','SUBSCRIBES','USES_SCHEMA','CONTAINS'}]
        return {"summary":{"nodes":len(nodes),"edges":len(edges),"layers":["requirement","architecture","contract","flow"]},"nodes":nodes,"edges":edges}
    def module_contract(self,module:str,depth:int=2)->dict:
        hits=self.g.find(module,['MODULE']);
        if not hits:return {"warnings":[f"No module matches {module}"],"nodes":[],"edges":[]}
        nodes,edges=self.g.neighborhood([n.id for n in hits],depth,['CONTAINS','EXPOSES_API','CONSUMES_API','PUBLISHES','SUBSCRIBES','USES_SCHEMA','IMPORTS'])
        return pack(nodes,edges,module=module,depth=depth)
    def _trace(self,term:str,types:list[str],depth:int=5)->dict:
        starts=self.g.find(term)
        starts=[n for n in starts if n.kind in {'EVENT','API','SYMBOL','SCHEMA','MODULE'}]
        if not starts:return {"warnings":[f"No graph node matches {term}"],"nodes":[],"edges":[],"paths":[]}
        nodes,edges=self.g.neighborhood([n.id for n in starts],depth,types)
        return pack(nodes,edges,paths=self._paths([n.id for n in starts],edges,depth))
    def trace_flow(self,term:str,depth:int=5): return self._trace(term,['CALLS','READS','WRITES','DATA_FLOWS_TO','EVENT_FLOWS_TO','PUBLISHES','SUBSCRIBES'],depth)
    def trace_data(self,term:str,depth:int=5): return self._trace(term,['READS','WRITES','DATA_FLOWS_TO','CALLS','USES_SCHEMA'],depth)
    def trace_event(self,term:str,depth:int=5): return self._trace(term,['PUBLISHES','SUBSCRIBES','EVENT_FLOWS_TO','CALLS'],depth)
    def symbol_impact(self,symbol:str,depth:int=4)->dict:
        hits=self.g.find(symbol,['SYMBOL','API','EVENT']); nodes,edges=self.g.reverse_impact([n.id for n in hits],depth) if hits else ([],[])
        return pack(nodes,edges,query=symbol,warnings=[] if hits else [f"No symbol/API/event matches {symbol}"])
    def contract_impact(self,contract:str,depth:int=4)->dict:
        hits=self.g.find(contract,['API','EVENT','SCHEMA']); nodes,edges=self.g.reverse_impact([n.id for n in hits],depth) if hits else ([],[])
        return pack(nodes,edges,query=contract,warnings=[] if hits else [f"No contract matches {contract}"])
    def requirement_impact(self,requirement:str,requirement_id:str='REQ-new')->dict:
        req=nid('REQUIREMENT',requirement_id); self.g.upsert_node(Node(req,'REQUIREMENT',requirement_id,requirement_id,'',{'text':requirement}))
        words={w.lower() for w in requirement.replace('_',' ').split() if len(w)>2}; candidates=[]
        for n in [Node(**x) for x in self.g.export()['nodes']]:
            if n.kind not in {'MODULE','SYMBOL','API','EVENT','SCHEMA'}:continue
            label=(n.name+' '+n.qualified_name).lower(); score=sum(w in label for w in words)/max(1,len(words))
            if score: candidates.append((score,n))
        candidates=sorted(candidates,key=lambda x:(-x[0],x[1].id))[:8]
        for score,n in candidates: self.g.upsert_edge(Edge(n.id,req,'IMPLEMENTS_REQUIREMENT',round(min(.85,.35+score),2),'requirement_match',f'keyword overlap: {requirement[:100]}'))
        self.g.commit(); nodes,edges=self.g.neighborhood([req],2)
        return pack(nodes,edges,requirement=requirement,candidates=[{'node':n.id,'confidence':s} for s,n in candidates])
    def simulate_change(self,description:str,kind:str='MODULE',targets:list[str]|None=None)->dict:
        if kind not in SIMULATION_KINDS:
            raise ValueError(f"simulate_change kind must be one of: {', '.join(sorted(SIMULATION_KINDS))}")
        sim=nid(kind,f'hypothetical:{description[:80]}'); self.g.upsert_node(Node(sim,kind,description[:80],sim,'',{'hypothetical':True,'description':description}))
        if not targets:
            terms=[w for w in description.split() if len(w)>3]; targets=[n.id for t in terms for n in self.g.find(t,['MODULE','SYMBOL','API','EVENT','SCHEMA']) if n.id != sim][:6]
        for target in dict.fromkeys(targets or []):
            if target == sim: continue
            typ='EXPOSES_API' if kind=='API' else 'PUBLISHES' if kind=='EVENT' else 'DEPENDS_ON'
            self.g.upsert_edge(Edge(sim,target,typ,.35,'simulation',description,attrs={'hypothetical':True}))
        self.g.commit(); nodes,edges=self.g.neighborhood([sim],3); affected,impact=self.g.reverse_impact([x.id for x in nodes if x.id != sim],3)
        return pack(nodes+ [n for n in affected if n.id not in {x.id for x in nodes}],edges+impact,simulation=sim,assumptions=['Edges are hypothetical with confidence 0.35; validate contracts and call sites before implementation.'])
    @staticmethod
    def graph_diff(before:str,after:str)->dict:
        a=json.loads(Path(before).read_text(encoding='utf-8')); b=json.loads(Path(after).read_text(encoding='utf-8'))
        def key(x:dict,edge:bool=False): return (x['source'],x['target'],x['type'],x.get('provenance',''),x.get('evidence','')) if edge else x['id']
        an={key(x):x for x in a['nodes']}; bn={key(x):x for x in b['nodes']}; ae={key(x,True):x for x in a['edges']}; be={key(x,True):x for x in b['edges']}
        return {'nodes_added':[bn[k] for k in bn.keys()-an.keys()],'nodes_removed':[an[k] for k in an.keys()-bn.keys()],'edges_added':[be[k] for k in be.keys()-ae.keys()],'edges_removed':[ae[k] for k in ae.keys()-be.keys()]}
    def git_diff_impact(self,repo:str,rev:str='HEAD~1..HEAD',cochange:bool=False)->dict:
        root=Path(repo).resolve()
        try: out=subprocess.check_output(['git','-C',str(root),'diff','--name-only',rev],text=True,stderr=subprocess.STDOUT)
        except (subprocess.CalledProcessError,FileNotFoundError) as e:return {'warnings':[f'Git diff unavailable: {e}'],'nodes':[],'edges':[]}
        changed=[x.strip().replace('\\','/') for x in out.splitlines() if x.strip()]; starts=[]
        for file in changed: starts += [n.id for n in self.g.find(file,['FILE','MODULE'])]
        nodes,edges=self.g.reverse_impact(list(dict.fromkeys(starts)),4)
        if cochange:self._cochanges(root)
        return pack(nodes,edges,revision=rev,changed_files=changed)
    def _cochanges(self,repo:Path)->None:
        try: out=subprocess.check_output(['git','-C',str(repo),'log','--pretty=format:COMMIT:%H','--name-only','-n','40'],text=True)
        except subprocess.CalledProcessError:return
        commit=None; files=[]
        for line in out.splitlines()+['COMMIT:END']:
            if line.startswith('COMMIT:'):
                if commit:
                    ids=[n.id for f in files for n in self.g.find(f,['FILE'])]
                    for i,x in enumerate(ids):
                        for y in ids[i+1:]:self.g.upsert_edge(Edge(x,y,'CO_CHANGES_WITH',.4,'git',','.join(files),commit))
                commit=line[7:]; files=[]
            elif line.strip():files.append(line.strip())
        self.g.commit()
    def verify_spec(self,spec:dict)->dict:
        requirements=spec.get('requirements') if isinstance(spec,dict) else None
        if not isinstance(requirements,list) or not requirements:
            return {'valid':False,'checked':[],'gaps':[{'requirement':None,'missing_evidence':True,'missing_modules':[],'error':'spec requires a non-empty requirements list'}],'warnings':['MVP verification checks graph coverage, not behavioral correctness.']}
        missing=[]; checked=[]
        for req in requirements:
            if not isinstance(req,dict):
                missing.append({'requirement':None,'missing_evidence':True,'missing_modules':[],'error':'requirement entries must be objects'})
                continue
            # A spec is self-describing: do not require a previous mutating
            # requirement-impact call merely to create a REQUIREMENT node.
            # Coverage is established by the declared must_touch modules and
            # by at least one graph fact sharing a meaningful term with its text.
            text=req.get('text','')
            modules=req.get('must_touch',[])
            if not isinstance(text,str) or not text.strip() or not isinstance(modules,list) or not modules or not all(isinstance(m,str) and m.strip() for m in modules):
                missing.append({'requirement':req.get('id',text),'missing_evidence':True,'missing_modules':[],'error':'each requirement needs text and a non-empty must_touch list'})
                checked.append(req.get('id',text))
                continue
            words={w.lower() for w in text.replace('_',' ').split() if len(w)>2}
            matches=[]
            for node in (Node(**x) for x in self.g.export()['nodes']):
                label=(node.name+' '+node.qualified_name).lower()
                if node.kind in {'MODULE','SYMBOL','API','EVENT','SCHEMA'} and any(w in label for w in words):
                    matches.append(node)
            absent=[m for m in modules if not self.g.find(m,['MODULE'])]
            if not matches or absent: missing.append({'requirement':req.get('id',req.get('text')),'missing_evidence':not bool(matches),'missing_modules':absent})
            checked.append(req.get('id',req.get('text')))
        return {'valid':not missing,'checked':checked,'gaps':missing,'warnings':['MVP verification checks graph coverage, not behavioral correctness.']}
    @staticmethod
    def _paths(starts:list[str],edges:list[Edge],depth:int)->list[list[str]]:
        adj=defaultdict(list)
        for e in edges:adj[e.source].append(e.target);adj[e.target].append(e.source)
        found=[]
        for start in starts:
            stack=[(start,[start])]
            while stack:
                node,path=stack.pop()
                if len(path)>1:found.append(path)
                if len(path)<=depth:
                    stack.extend((n,path+[n]) for n in adj[node] if n not in path)
        return found[:30]

def nid(kind:str,value:str)->str:return f'{kind}:{value.replace(chr(92),"/")}'
