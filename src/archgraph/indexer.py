from __future__ import annotations

import ast
import json
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from .graph import GraphStore
from .model import Edge, Node

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx"}
CONTRACT_SUFFIXES = {".json", ".openapi.json", ".asyncapi.json", ".graphql", ".gql", ".proto"}

def nid(kind: str, value: str) -> str: return kind + ':' + value.replace('\\', '/')
def module_name(path: Path) -> str:
    clean=str(path.with_suffix('')).replace('\\','/').replace('/','.')
    return clean[:-9] if clean.endswith('.__init__') else clean

@dataclass
class IndexOptions:
    root: Path
    reset: bool = True

class Indexer:
    def __init__(self, graph: GraphStore, root: str | Path): self.graph,self.root=graph,Path(root).resolve()
    def index(self, reset: bool = True) -> dict:
        if reset: self.graph.clear()
        source_files=[]; contract_files=[]
        for path in self.root.rglob('*'):
            if not path.is_file() or any(p in {'.git','.venv','node_modules','__pycache__'} for p in path.parts): continue
            lower=path.name.lower()
            if path.suffix.lower() in SOURCE_SUFFIXES: source_files.append(path)
            elif path.suffix.lower() in CONTRACT_SUFFIXES or lower.endswith(('.openapi.json','.asyncapi.json')): contract_files.append(path)
        # A JS/TS import can be a Router host whose factory declaration lives
        # in a different source file.  Keep exports keyed by their source
        # module: a same-named export elsewhere in the repository must not
        # reclassify an unrelated imported client as a route host.
        exported_route_aliases_by_module={}
        for path in source_files:
            if path.suffix != '.py':
                key=path.relative_to(self.root).with_suffix('').as_posix()
                exported_route_aliases_by_module[key]=JavaScriptAdapter.exported_route_aliases(path.read_text(encoding='utf-8',errors='replace'))
        for path in source_files: self._source(path, exported_route_aliases_by_module)
        for path in contract_files: self._contract(path)
        self.graph.commit()
        return {"root":str(self.root),"sources":len(source_files),"contracts":len(contract_files),"nodes":len(self.graph.export()['nodes']),"edges":len(self.graph.export()['edges'])}
    def _file_module(self,path:Path)->tuple[str,str,str]:
        rel=path.relative_to(self.root); f=nid('FILE',str(rel)); mod=module_name(rel)
        m=nid('MODULE',mod)
        self.graph.upsert_node(Node(f,'FILE',path.name,str(rel),str(rel)))
        self.graph.upsert_node(Node(m,'MODULE',mod,mod,str(rel)))
        self.graph.upsert_edge(Edge(m,f,'CONTAINS',1,'ast',str(rel)))
        return f,m,mod
    def _source(self,path:Path,exported_route_aliases_by_module:dict[str,set[str]]|None=None)->None:
        _,mod_id,mod=self._file_module(path); text=path.read_text(encoding='utf-8',errors='replace')
        if path.suffix=='.py': PythonAdapter(self.graph,mod_id,mod,path.relative_to(self.root),text).parse()
        else: JavaScriptAdapter(self.graph,mod_id,mod,path.relative_to(self.root),text,exported_route_aliases_by_module=exported_route_aliases_by_module or {}).parse()
    def _contract(self,path:Path)->None:
        _,module,mod=self._file_module(path); text=path.read_text(encoding='utf-8',errors='replace')
        ContractAdapter(self.graph,module,mod,path.relative_to(self.root),text).parse(path)

class LanguageAdapter:
    """Replaceable seam for Tree-sitter or a language-server implementation."""
    def __init__(self,g:GraphStore,module:str,modname:str,path:Path,text:str): self.g,self.module,self.modname,self.path,self.text=g,module,modname,path,text
    def symbol(self,name:str,line:int,kind:str='function',qualified_name:str|None=None)->str:
        q=f'{self.modname}.{qualified_name or name}'; sid=nid('SYMBOL',q)
        self.g.upsert_node(Node(sid,'SYMBOL',name,q,str(self.path),{'language':self.path.suffix,'symbol_kind':kind,'line':line}))
        self.g.upsert_edge(Edge(self.module,sid,'CONTAINS',1,'ast',f'{self.path}:{line}'))
        return sid
    def event(self,name:str,line:int)->str:
        eid=nid('EVENT',name); self.g.upsert_node(Node(eid,'EVENT',name,name,str(self.path)))
        return eid

class PythonAdapter(LanguageAdapter):
    def parse(self)->None:
        try: tree=ast.parse(self.text)
        except SyntaxError as exc:
            self.g.upsert_node(Node(nid('TASK',f'parse:{self.path}'),'TASK','parse warning','',str(self.path),{'warning':str(exc)})); return
        # Index executable scopes separately.  Scanning a ClassDef as a single
        # body makes calls in its methods appear to originate from the class,
        # and loses the method that owns a route decorator.
        local={}
        symbols_by_node={}
        for n in tree.body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                sid=self.symbol(n.name,n.lineno,'class' if isinstance(n,ast.ClassDef) else 'function')
                local[n.name]=sid; symbols_by_node[id(n)]=sid
            if isinstance(n,ast.ClassDef):
                for member in n.body:
                    if isinstance(member,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        symbols_by_node[id(member)]=self.symbol(member.name,member.lineno,'method',f'{n.name}.{member.name}')
        imports={}
        for n in ast.walk(tree):
            if isinstance(n,ast.Import):
                for alias in n.names: imports[alias.asname or alias.name.split('.')[0]]=alias.name; self._import(alias.name,n.lineno)
            elif isinstance(n,ast.ImportFrom):
                base=n.module or ''
                self._import(base,n.lineno)
                for alias in n.names: imports[alias.asname or alias.name]=f'{base}.{alias.name}'
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                self._body(symbols_by_node[id(node)],node,local,imports)
            elif isinstance(node,ast.ClassDef):
                for member in node.body:
                    if isinstance(member,(ast.FunctionDef,ast.AsyncFunctionDef)):
                        self._body(symbols_by_node[id(member)],member,local,imports)
        self._routes(tree,symbols_by_node)
    def _import(self,target:str,line:int)->None:
        if not target:return
        mid=nid('MODULE',target); self.g.upsert_node(Node(mid,'MODULE',target,target))
        self.g.upsert_edge(Edge(self.module,mid,'IMPORTS',.95,'ast',f'{self.path}:{line}'))
    def _body(self,src:str,node:ast.AST,local:dict,imports:dict)->None:
        # A framework route decorator such as ``@app.post('/orders')`` is an
        # exposure, not a call made by this function to consume that API.
        decorator_calls={id(call) for decorator in getattr(node,'decorator_list',[]) for call in ast.walk(decorator) if isinstance(call,ast.Call)}
        for n in ast.walk(node):
            if isinstance(n,ast.Call):
                name=self._callname(n.func)
                if name:
                    target=local.get(name) or nid('SYMBOL',imports.get(name,name))
                    if not local.get(name): self.g.upsert_node(Node(target,'SYMBOL',name,imports.get(name,name)))
                    self.g.upsert_edge(Edge(src,target,'CALLS',.9 if name in local else .5,'ast',f'{self.path}:{n.lineno}'))
                    self._event_call(src,name,n)
                    if id(n) not in decorator_calls: self._api_call(src,n)
            elif isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load):
                target=nid('SYMBOL',f'{self.modname}.{n.id}'); self.g.upsert_node(Node(target,'SYMBOL',n.id,f'{self.modname}.{n.id}',str(self.path)))
                self.g.upsert_edge(Edge(src,target,'READS',.45,'ast',f'{self.path}:{n.lineno}'))
                self.g.upsert_edge(Edge(target,src,'DATA_FLOWS_TO',.4,'ast',f'{self.path}:{n.lineno}'))
            elif isinstance(n,ast.Name) and isinstance(n.ctx,(ast.Store,ast.Del)):
                target=nid('SYMBOL',f'{self.modname}.{n.id}'); self.g.upsert_node(Node(target,'SYMBOL',n.id,f'{self.modname}.{n.id}',str(self.path)))
                self.g.upsert_edge(Edge(src,target,'WRITES',.45,'ast',f'{self.path}:{n.lineno}'))
                self.g.upsert_edge(Edge(src,target,'DATA_FLOWS_TO',.4,'ast',f'{self.path}:{n.lineno}'))
    def _event_call(self,src:str,name:str,n:ast.Call)->None:
        if name not in {'publish','emit','send','subscribe','on','consume'} or not n.args:return
        if isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str):
            event=self.event(n.args[0].value,n.lineno); typ='PUBLISHES' if name in {'publish','emit','send'} else 'SUBSCRIBES'
            self.g.upsert_edge(Edge(src,event,typ,.82,'ast',f'{self.path}:{n.lineno}'))
            self.g.upsert_edge(Edge(src,event,'EVENT_FLOWS_TO',.78,'ast',f'{self.path}:{n.lineno}'))
    def _api_call(self,src:str,n:ast.Call)->None:
        """Record explicit HTTP-client calls as module/symbol API consumption."""
        if not isinstance(n.func,ast.Attribute) or n.func.attr.lower() not in {'get','post','put','patch','delete'} or not n.args:return
        if not isinstance(n.args[0],ast.Constant) or not isinstance(n.args[0].value,str):return
        method=n.func.attr.upper(); route=n.args[0].value; api=nid('API',f'{method} {route}')
        self.g.upsert_node(Node(api,'API',f'{method} {route}',f'{method} {route}',str(self.path)))
        self.g.upsert_edge(Edge(src,api,'CONSUMES_API',.75,'ast',f'{self.path}:{n.lineno}'))
    def _routes(self,tree:ast.AST,symbols_by_node:dict[int,str])->None:
        route_functions=[]
        for n in tree.body:
            if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
                route_functions.append(n)
            elif isinstance(n,ast.ClassDef):
                route_functions.extend(member for member in n.body if isinstance(member,(ast.FunctionDef,ast.AsyncFunctionDef)))
        for n in route_functions:
            for dec in n.decorator_list:
                if isinstance(dec,ast.Call) and isinstance(dec.func,ast.Attribute) and dec.func.attr.lower() in {'get','post','put','delete','patch','route'} and dec.args and isinstance(dec.args[0],ast.Constant):
                    method=dec.func.attr.upper(); route=str(dec.args[0].value); api=nid('API',f'{method} {route}')
                    self.g.upsert_node(Node(api,'API',f'{method} {route}',f'{method} {route}',str(self.path)))
                    self.g.upsert_edge(Edge(symbols_by_node[id(n)],api,'EXPOSES_API',.9,'ast',f'{self.path}:{n.lineno}'))
    @staticmethod
    def _callname(n:ast.AST)->str:
        return n.id if isinstance(n,ast.Name) else n.attr if isinstance(n,ast.Attribute) else ''

class JavaScriptAdapter(LanguageAdapter):
    """Conservative no-dependency fallback; replace with TypeScript/Tree-sitter adapter later."""
    DECL=re.compile(r'(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)')
    IMP=re.compile(r"(?:import\s+(?:[^;]*?\s+from\s+)?|require\()['\"]([^'\"]+)['\"]")
    HTTP_CALL=re.compile(r"\b(?P<receiver>[A-Za-z_$][\w$]*)\s*\.\s*(?P<method>get|post|put|delete|patch)\s*\(\s*['\"](?P<route>[^'\"]+)",re.I)
    ROUTE_RECEIVERS={'app','router','server','application','express'}
    CLIENT_RECEIVERS={'api','client','http','https','axios','request','requests','fetcher'}
    # The declaration itself is stronger evidence than a client-like name.
    # It deliberately accepts a TypeScript type annotation between the alias
    # and its Router/express factory assignment.
    ROUTE_FACTORY=r'(?:(?:[A-Za-z_$][\w$]*\s*\.\s*)?(?:Router|[A-Za-z_$][\w$]*Router)\s*\(|express\s*\(|require\s*\(\s*[\'\"]express[\'\"]\s*\)\s*\.\s*Router\s*\()'
    ROUTE_ALIAS=re.compile(r'\b(?:export\s+)?(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)(?:\s*:\s*[^=;\n]+)?\s*=\s*'+ROUTE_FACTORY,re.I)
    ROUTE_ASSIGNMENT=re.compile(r'\b(?P<alias>[A-Za-z_$][\w$]*)\s*=\s*'+ROUTE_FACTORY,re.I)
    ROUTE_TERNARY_ALIAS=re.compile(r'\b(?:export\s+)?(?:const|let|var)\s+(?P<alias>[A-Za-z_$][\w$]*)(?:\s*:\s*[^=;\n]+)?\s*=\s*[^;\n]*\?\s*'+ROUTE_FACTORY+r'[^:;\n]*:\s*'+ROUTE_FACTORY,re.I)
    IMPORT_BINDINGS=re.compile(r'\bimport\s+(?P<bindings>[^;\n]+?)\s+from\s*[\'\"](?P<source>[^\'\"]+)[\'\"]',re.I)

    def __init__(self,*args,exported_route_aliases_by_module:dict[str,set[str]]|None=None):
        super().__init__(*args)
        self.exported_route_aliases_by_module={
            module: {name.lower() for name in aliases}
            for module,aliases in (exported_route_aliases_by_module or {}).items()
        }

    @classmethod
    def exported_route_aliases(cls,text:str)->set[str]:
        return {match.group('alias') for match in cls.ROUTE_ALIAS.finditer(text)
                if re.search(r'\bexport\s+(?:const|let|var)\s+'+re.escape(match.group('alias'))+r'\b', match.group(0), re.I)}

    def _imported_route_aliases(self)->set[str]:
        aliases=set()
        for match in self.IMPORT_BINDINGS.finditer(self.text):
            bindings=match.group('bindings').strip()
            source=match.group('source')
            # This deliberately resolves only a direct relative named import.
            # Default imports, renamed imports, re-export chains, and package
            # aliases are ambiguous under this lexical fallback and are left
            # unclassified rather than exposed as routes.
            if not source.startswith('.') or not bindings.startswith('{'):
                continue
            # Module-export keys are root-relative POSIX paths.  A relative
            # import may legally contain ``..``; normalize it before lookup so
            # ``web/../host`` resolves to the same key as ``host``.  Do not use
            # Path.resolve(): fixtures and source trees need not exist outside
            # the indexed root, and that would make this lexical fallback host
            # filesystem-dependent.
            module=posixpath.normpath((self.path.parent / source).with_suffix('').as_posix())
            exported=self.exported_route_aliases_by_module.get(module,set())
            for part in bindings.strip('{} ').split(','):
                imported=part.strip()
                if not imported or ' as ' in imported:
                    continue
                if imported.lower() in exported:
                    aliases.add(imported.lower())
        return aliases

    def _local_route_aliases(self)->set[str]:
        return {match.group('alias').lower() for pattern in (self.ROUTE_ALIAS,self.ROUTE_ASSIGNMENT,self.ROUTE_TERNARY_ALIAS) for match in pattern.finditer(self.text)}

    @classmethod
    def _http_relation(cls,receiver:str,route_receivers:set[str])->str|None:
        """Classify only unambiguous lexical receivers; omit unknown calls."""
        lower=receiver.lower()
        if lower in route_receivers or lower in cls.ROUTE_RECEIVERS or lower.endswith(('app','router','server')):
            return 'EXPOSES_API'
        if lower in cls.CLIENT_RECEIVERS or any(token in lower for token in ('client','http','axios','request')):
            return 'CONSUMES_API'
        return None

    def parse(self)->None:
        # ``api`` is a common client name, but an explicit Router/express
        # assignment is stronger lexical evidence that it is a route host.
        route_receivers=set(self.ROUTE_RECEIVERS)
        route_receivers.update(self._local_route_aliases())
        route_receivers.update(self._imported_route_aliases())
        symbols={m.group(1):self.symbol(m.group(1),self.text[:m.start()].count('\n')+1,'class' if 'class' in m.group(0) else 'function') for m in self.DECL.finditer(self.text)}
        symbol_starts=sorted((m.start(),symbols[m.group(1)]) for m in self.DECL.finditer(self.text))
        def owner(position:int)->str:
            earlier=[sid for start,sid in symbol_starts if start <= position]
            return earlier[-1] if earlier else self.module
        for m in self.IMP.finditer(self.text):
            target=m.group(1); mid=nid('MODULE',target); self.g.upsert_node(Node(mid,'MODULE',target,target)); self.g.upsert_edge(Edge(self.module,mid,'IMPORTS',.75,'lexical',f'{self.path}:{self.text[:m.start()].count(chr(10))+1}'))
        for name,src in symbols.items():
            pos=self.text.find(name); end=self.text.find('\n}',pos); body=self.text[pos:end if end>pos else len(self.text)]
            for call in re.finditer(r'\b([A-Za-z_$][\w$]*)\s*\(',body):
                called=call.group(1)
                if called in {'if','for','while','function','catch'}: continue
                target=symbols.get(called,nid('SYMBOL',called)); self.g.upsert_edge(Edge(src,target,'CALLS',.7 if called in symbols else .4,'lexical',f'{self.path}'))
            for match in re.finditer(r"(?:\.|\b)(publish|emit|send|subscribe|on)\s*\(\s*['\"]([^'\"]+)",body):
                event=self.event(match.group(2),1); typ='PUBLISHES' if match.group(1) in {'publish','emit','send'} else 'SUBSCRIBES'; self.g.upsert_edge(Edge(src,event,typ,.72,'lexical',f'{self.path}'))
        for m in self.HTTP_CALL.finditer(self.text):
            relation=self._http_relation(m.group('receiver'),route_receivers)
            # A bare lexical method call is ambiguous.  Do not invent a client
            # relationship; route hosts and recognizable client receivers are
            # the only evidence this fallback accepts.
            if relation is None: continue
            method=m.group('method').upper(); route=m.group('route'); api=nid('API',f'{method} {route}')
            self.g.upsert_node(Node(api,'API',f'{method} {route}',f'{method} {route}',str(self.path)))
            self.g.upsert_edge(Edge(owner(m.start()),api,relation,.55,'lexical',f'{self.path}:{self.text[:m.start()].count(chr(10))+1}'))

class ContractAdapter:
    """Adapter boundary for OpenAPI, AsyncAPI, GraphQL and Protobuf contracts."""
    def __init__(self,g:GraphStore,module:str,modname:str,path:Path,text:str): self.g,self.module,self.modname,self.path,self.text=g,module,modname,path,text
    def parse(self,path:Path)->None:
        suffix=path.suffix.lower(); lower=path.name.lower()
        if suffix=='.proto': self._proto()
        elif suffix in {'.graphql','.gql'}: self._graphql()
        elif suffix=='.json' or lower.endswith(('.openapi.json','.asyncapi.json')):
            try: doc=json.loads(self.text)
            except json.JSONDecodeError:return
            if 'openapi' in doc: self._openapi(doc)
            if 'asyncapi' in doc: self._asyncapi(doc)
    def _api(self,method:str,path:str,evidence:str)->str:
        api=nid('API',f'{method.upper()} {path}'); self.g.upsert_node(Node(api,'API',f'{method.upper()} {path}',f'{method.upper()} {path}',str(self.path))); self.g.upsert_edge(Edge(self.module,api,'EXPOSES_API',1,'contract_adapter',evidence)); return api
    def _schema(self,name:str)->str:
        s=nid('SCHEMA',name); self.g.upsert_node(Node(s,'SCHEMA',name,name,str(self.path))); return s
    def _openapi(self,d:dict)->None:
        for path,item in d.get('paths',{}).items():
            for method,op in item.items():
                if method.lower() not in {'get','post','put','patch','delete','head'}:continue
                api=self._api(method,path,f'{self.path}#/paths/{path}');
                for ref in re.findall(r'#/components/schemas/([^"\\s/]+)',json.dumps(op)): self.g.upsert_edge(Edge(api,self._schema(ref),'USES_SCHEMA',.95,'contract_adapter',str(self.path)))
    def _asyncapi(self,d:dict)->None:
        for channel,item in d.get('channels',{}).items():
            event=self._event(channel)
            for direction in ('publish','subscribe'):
                if direction in item: self.g.upsert_edge(Edge(self.module,event,'PUBLISHES' if direction=='publish' else 'SUBSCRIBES',1,'contract_adapter',f'{self.path}#/channels/{channel}'))
    def _event(self,name:str)->str:
        e=nid('EVENT',name); self.g.upsert_node(Node(e,'EVENT',name,name,str(self.path))); return e
    def _graphql(self)->None:
        for typ,body in re.findall(r'(?:type|interface)\s+(\w+)\s*\{([^}]*)\}',self.text,re.S):
            schema=self._schema(typ)
            for field in re.findall(r'(\w+)\s*(?:\([^)]*\))?\s*:',body):
                api=self._api('GRAPHQL',f'{typ}.{field}',str(self.path)); self.g.upsert_edge(Edge(api,schema,'USES_SCHEMA',.8,'contract_adapter',str(self.path)))
    def _proto(self)->None:
        for name in re.findall(r'\bmessage\s+(\w+)',self.text): self._schema(name)
        for service,body in re.findall(r'\bservice\s+(\w+)\s*\{([^}]*)\}',self.text,re.S):
            for rpc,req,res in re.findall(r'\brpc\s+(\w+)\s*\((\w+)\)\s+returns\s+\((\w+)\)',body):
                api=self._api('RPC',f'{service}.{rpc}',str(self.path)); self.g.upsert_edge(Edge(api,self._schema(req),'USES_SCHEMA',1,'contract_adapter',str(self.path))); self.g.upsert_edge(Edge(api,self._schema(res),'USES_SCHEMA',1,'contract_adapter',str(self.path)))
