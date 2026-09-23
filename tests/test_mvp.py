import copy
import inspect
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archgraph.graph import GraphStore
from archgraph.indexer import Indexer
from archgraph.mcp import TOOL_SCHEMAS, call, tool_defs
from archgraph.service import AnalysisService
from archgraph.cli import TOOLS, build_parser

ROOT = Path(__file__).parents[1] / 'examples' / 'commerce'
PROJECT = Path(__file__).parents[1]

class MVPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'graph.db'
        g=GraphStore(self.db); self.summary=Indexer(g,ROOT).index();g.close();self.svc=AnalysisService(self.db)
    def tearDown(self):self.svc.close();self.tmp.cleanup()
    def run_cli(self,*args,expected=0):
        result=subprocess.run([sys.executable,'-m','archgraph.cli',*args],cwd=PROJECT,text=True,capture_output=True)
        self.assertEqual(result.returncode,expected,msg=f'command: {args}\nstdout: {result.stdout}\nstderr: {result.stderr}')
        return result
    def indexed_facts(self,fixture_name,files):
        fixture=Path(self.tmp.name)/fixture_name; fixture.mkdir()
        for name,content in files.items():
            path=fixture/name; path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(content,encoding='utf-8')
        db=Path(self.tmp.name)/f'{fixture_name}.db'; graph=GraphStore(db); Indexer(graph,fixture).index()
        facts={(edge['source'],edge['target'],edge['type'],edge['provenance']) for edge in graph.export()['edges']}; graph.close()
        return facts
    def test_index_emits_expected_dependency_and_contract_facts(self):
        graph=self.svc.g.export()
        kinds={n['kind'] for n in graph['nodes']}
        self.assertTrue({'MODULE','FILE','API','EVENT','SCHEMA','SYMBOL'} <= kinds)
        facts={(e['source'],e['target'],e['type'],e['provenance']) for e in graph['edges']}
        self.assertIn(('MODULE:src.orders','MODULE:events','IMPORTS','ast'),facts)
        self.assertIn(('MODULE:src.orders','MODULE:payments','IMPORTS','ast'),facts)
        self.assertIn(('MODULE:openapi','API:POST /orders','EXPOSES_API','contract_adapter'),facts)
        self.assertIn(('MODULE:asyncapi','EVENT:order.created','PUBLISHES','contract_adapter'),facts)
        # Check both independent read and write emit sites, so either one
        # becoming a no-op makes this test fail rather than being masked by the other.
        self.assertIn(('SYMBOL:src.orders.create_order','SYMBOL:src.orders.receipt','DATA_FLOWS_TO','ast'),facts)
        self.assertIn(('SYMBOL:src.orders.receipt','SYMBOL:src.orders.create_order','DATA_FLOWS_TO','ast'),facts)
        self.assertIn(('MODULE:src.notifications','MODULE:./bus','IMPORTS','lexical'),facts)
    def test_python_import_forms_emit_independently(self):
        fixture=Path(self.tmp.name)/'imports'; fixture.mkdir()
        (fixture/'imports.py').write_text('import payments\nfrom events import publish\n',encoding='utf-8')
        db=Path(self.tmp.name)/'imports.db'; graph=GraphStore(db); Indexer(graph,fixture).index()
        facts={(edge['source'],edge['target'],edge['type'],edge['provenance']) for edge in graph.export()['edges']}; graph.close()
        self.assertIn(('MODULE:imports','MODULE:payments','IMPORTS','ast'),facts)
        self.assertIn(('MODULE:imports','MODULE:events','IMPORTS','ast'),facts)
    def test_event_trace(self):
        result=self.svc.trace_event('order.created')
        facts={(e['source'],e['target'],e['type'],e['provenance']) for e in result['edges']}
        self.assertIn(('SYMBOL:src.orders.create_order','EVENT:order.created','PUBLISHES','ast'),facts)
        self.assertIn(('SYMBOL:src.orders.create_order','EVENT:order.created','EVENT_FLOWS_TO','ast'),facts)
        self.assertIn(('SYMBOL:src.notifications.notifyCustomer','EVENT:order.created','SUBSCRIBES','lexical'),facts)
    def test_flow_and_data_traces_contain_emitted_flow_edges(self):
        flow=self.svc.trace_flow('src.orders.create_order',3)
        data=self.svc.trace_data('src.orders.create_order',3)
        self.assertTrue(any(e['type']=='DATA_FLOWS_TO' and e['provenance']=='ast' for e in flow['edges']))
        self.assertTrue(any(e['type']=='EVENT_FLOWS_TO' and e['provenance']=='ast' for e in flow['edges']))
        self.assertTrue(any(e['type']=='DATA_FLOWS_TO' and e['provenance']=='ast' for e in data['edges']))
    def test_requirement_and_simulation(self):
        result=self.svc.requirement_impact('notify customer after order created','REQ-1')
        self.assertTrue(result['candidates'])
        sim=self.svc.simulate_change('add an order created webhook', 'EVENT')
        self.assertIn('simulation',sim)
    def test_spec_coverage_is_independently_valid(self):
        spec=json.loads((ROOT/'spec.json').read_text())
        result=self.svc.verify_spec(spec)
        self.assertTrue(result['valid'],result)
        self.assertFalse(result['gaps'],result)
    def test_spec_coverage_rejects_invalid_or_uncovered_requirements(self):
        for spec in ({}, {'requirements':[]}, {'requirements':[{'id':'REQ-X','text':'unimplemented zzz feature','must_touch':['src.orders']}]}, {'requirements':[{'id':'REQ-Y','text':'order','must_touch':['src.does.not.exist']}]}):
            result=self.svc.verify_spec(spec)
            self.assertFalse(result['valid'],result)
            self.assertTrue(result['gaps'],result)
    def test_cli_verify_spec_rejects_constant_true_and_constant_false_substitutes(self):
        valid=json.loads((ROOT/'spec.json').read_text())
        invalid={'requirements':[{'id':'REQ-X','text':'unimplemented zzz feature','must_touch':['src.orders']}]}
        valid_path=Path(self.tmp.name)/'valid-spec.json'; invalid_path=Path(self.tmp.name)/'invalid-spec.json'
        valid_path.write_text(json.dumps(valid),encoding='utf-8'); invalid_path.write_text(json.dumps(invalid),encoding='utf-8')
        valid_result=json.loads(self.run_cli('verify-spec','--spec',str(valid_path),'--db',str(self.db)).stdout)
        invalid_result=json.loads(self.run_cli('verify-spec','--spec',str(invalid_path),'--db',str(self.db)).stdout)
        self.assertTrue(valid_result['valid'],valid_result)
        self.assertFalse(valid_result['gaps'],valid_result)
        self.assertFalse(invalid_result['valid'],invalid_result)
        self.assertTrue(invalid_result['gaps'],invalid_result)
    def test_graph_diff_detects_a_real_change(self):
        before=self.svc.g.export(); after=copy.deepcopy(before)
        after['nodes'].append({'id':'MODULE:added','kind':'MODULE','name':'added','qualified_name':'added','file_path':'','attrs':{}})
        before_path=Path(self.tmp.name)/'before.json'; after_path=Path(self.tmp.name)/'after.json'
        before_path.write_text(json.dumps(before),encoding='utf-8'); after_path.write_text(json.dumps(after),encoding='utf-8')
        direct=AnalysisService.graph_diff(str(before_path),str(after_path))
        self.assertEqual([node['id'] for node in direct['nodes_added']],['MODULE:added'])
        result=self.run_cli('graph-diff','--before',str(before_path),'--after',str(after_path))
        self.assertEqual([node['id'] for node in json.loads(result.stdout)['nodes_added']],['MODULE:added'])
    def test_cli_query_surface_and_readme_inputs_are_runnable(self):
        db=str(self.db); before=Path(self.tmp.name)/'before.json'; after=Path(self.tmp.name)/'after.json'
        commands=[
            (('project-map','--db',db), lambda payload: self.assertGreater(payload['summary']['nodes'],0)),
            (('module-contract','src.orders','--db',db), lambda payload: (self.assertEqual(payload['module'],'src.orders'),self.assertIn('MODULE:src.orders',{node['id'] for node in payload['nodes']}))),
            (('trace-flow','src.orders.create_order','--db',db), lambda payload: self.assertTrue({'DATA_FLOWS_TO','EVENT_FLOWS_TO'} <= {edge['type'] for edge in payload['edges']})),
            (('trace-data','src.orders.create_order','--db',db), lambda payload: self.assertIn('DATA_FLOWS_TO',{edge['type'] for edge in payload['edges']})),
            (('trace-event','order.created','--db',db), lambda payload: self.assertTrue({'PUBLISHES','SUBSCRIBES','EVENT_FLOWS_TO'} <= {edge['type'] for edge in payload['edges']})),
            (('requirement-impact','notify customer after order created','--db',db), lambda payload: (self.assertEqual(payload['requirement'],'notify customer after order created'),self.assertTrue(payload['candidates']))),
            (('symbol-impact','src.orders.create_order','--db',db), lambda payload: (self.assertEqual(payload['query'],'src.orders.create_order'),self.assertTrue(payload['nodes']))),
            (('contract-impact','POST /orders','--db',db), lambda payload: (self.assertEqual(payload['query'],'POST /orders'),self.assertTrue(payload['nodes']))),
            (('simulate-change','--description','add order webhook','--db',db), lambda payload: self.assertTrue(payload['simulation'].startswith('MODULE:hypothetical:add order webhook'))),
            (('git-diff-impact','--repo',str(ROOT),'--db',db), lambda payload: self.assertIn('Git diff unavailable',payload['warnings'][0])),
            (('verify-spec','--spec',str(ROOT/'spec.json'),'--db',db), lambda payload: (self.assertTrue(payload['valid']),self.assertFalse(payload['gaps']))),
        ]
        for command,assert_payload in commands:
            payload=json.loads(self.run_cli(*command).stdout)
            self.assertIsInstance(payload,dict)
            assert_payload(payload)
        snapshot=json.loads(self.run_cli('snapshot','--db',db,'--out',str(before)).stdout)
        self.assertEqual(snapshot,{'snapshot':str(before)})
        before_graph=json.loads(before.read_text(encoding='utf-8'))
        self.assertGreater(len(before_graph['nodes']),0)
        indexed=json.loads(self.run_cli('index',str(ROOT),'--db',db).stdout)
        self.assertEqual((indexed['sources'],indexed['contracts']),(4,5))
        self.assertGreater(indexed['edges'],0)
        self.run_cli('snapshot','--db',db,'--out',str(after))
        diff=json.loads(self.run_cli('graph-diff','--before',str(before),'--after',str(after)).stdout)
        self.assertIn('nodes_added',diff)
        self.assertEqual({node['id'] for node in diff['nodes_removed']},{'REQUIREMENT:REQ-new','MODULE:hypothetical:add order webhook'})
        missing=self.run_cli('module-contract','--db',db,expected=2)
        self.assertIn('required: module',missing.stderr)
        help_output=self.run_cli('requirement-impact','--help').stdout
        self.assertRegex(help_output,r'(?m)^\s+requirement$')
        self.assertNotIn(' query',help_output)
    def test_cli_module_contract_is_wired_to_service(self):
        result=json.loads(self.run_cli('module-contract','src.orders','--depth','1','--db',str(self.db)).stdout)
        self.assertEqual(result['module'],'src.orders')
        self.assertEqual(result['depth'],1)
        self.assertIn('MODULE:src.orders',{node['id'] for node in result['nodes']})
        self.assertIn('CONTAINS',{edge['type'] for edge in result['edges']})
    def test_readme_tool_examples_are_runnable_verbatim(self):
        readme=(PROJECT/'README.md').read_text(encoding='utf-8')
        block=re.search(r'## Tool examples\s*```powershell\s*(.*?)```',readme,re.S)
        self.assertIsNotNone(block,'README Tool examples PowerShell block is missing')
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory); shutil.copytree(ROOT,work/'examples'/'commerce')
            # The Tool examples follow Quick start, whose index command is their
            # documented prerequisite.  The commands inside the block are then
            # parsed from README and executed in their written order.
            initial=subprocess.run([sys.executable,'-m','archgraph.cli','index','.\\examples\\commerce','--db','.\\commerce.db'],cwd=work,text=True,capture_output=True)
            self.assertEqual(initial.returncode,0,initial.stderr)
            for line in block.group(1).strip().splitlines():
                parts=shlex.split(line, posix=False)
                self.assertEqual(parts[0],'archgraph',line)
                result=subprocess.run([sys.executable,'-m','archgraph.cli',*parts[1:]],cwd=work,text=True,capture_output=True)
                self.assertEqual(result.returncode,0,msg=f'README command: {line}\nstdout: {result.stdout}\nstderr: {result.stderr}')
    def test_p1_class_route_decorator_exposes_its_method_without_consuming(self):
        fixture=Path(self.tmp.name)/'class-route'; fixture.mkdir()
        (fixture/'svc.py').write_text('from fastapi import FastAPI\napp = FastAPI()\n\n@app.get("/orders")\ndef list_orders():\n    return []\n\nclass OrdersApi:\n    @app.get("/mine")\n    def mine(self):\n        return []\n',encoding='utf-8')
        db=Path(self.tmp.name)/'class-route.db'; graph=GraphStore(db); Indexer(graph,fixture).index(); facts={(e['source'],e['target'],e['type'],e['provenance']) for e in graph.export()['edges']}; graph.close()
        self.assertIn(('SYMBOL:svc.OrdersApi.mine','API:GET /mine','EXPOSES_API','ast'),facts)
        self.assertNotIn(('SYMBOL:svc.OrdersApi.mine','API:GET /mine','CONSUMES_API','ast'),facts)
        self.assertNotIn(('SYMBOL:svc.OrdersApi','API:GET /mine','CONSUMES_API','ast'),facts)

    def test_p2_python_http_client_call_still_consumes(self):
        fixture=Path(self.tmp.name)/'fixture'; fixture.mkdir()
        (fixture/'client.py').write_text('def send(client):\n    return client.post("/external")\n',encoding='utf-8')
        (fixture/'routes.py').write_text('@app.post("/orders")\ndef create():\n    return None\n',encoding='utf-8')
        db=Path(self.tmp.name)/'fixture.db'; graph=GraphStore(db); Indexer(graph,fixture).index(); facts={(e['source'],e['target'],e['type'],e['provenance']) for e in graph.export()['edges']}; graph.close()
        self.assertIn(('SYMBOL:client.send','API:POST /external','CONSUMES_API','ast'),facts)
        self.assertIn(('SYMBOL:routes.create','API:POST /orders','EXPOSES_API','ast'),facts)
        self.assertNotIn(('SYMBOL:routes.create','API:POST /orders','CONSUMES_API','ast'),facts)
    def test_p3_js_api_and_router_alias_route_hosts_do_not_consume(self):
        fixture=Path(self.tmp.name)/'js-fixture'; fixture.mkdir()
        (fixture/'client.ts').write_text('export function callOrders(client) { return client.post("/orders"); }\n',encoding='utf-8')
        (fixture/'routes.ts').write_text('const api = express.Router();\napi.post("/api-alias");\nconst handlers = Router();\nhandlers.post("/router-alias");\n',encoding='utf-8')
        db=Path(self.tmp.name)/'js-fixture.db'; graph=GraphStore(db); Indexer(graph,fixture).index(); facts={(e['source'],e['target'],e['type'],e['provenance']) for e in graph.export()['edges']}; graph.close()
        self.assertIn(('SYMBOL:client.callOrders','API:POST /orders','CONSUMES_API','lexical'),facts)
        self.assertIn(('SYMBOL:routes.api','API:POST /api-alias','EXPOSES_API','lexical'),facts)
        self.assertIn(('SYMBOL:routes.handlers','API:POST /router-alias','EXPOSES_API','lexical'),facts)
        self.assertFalse(any(edge[1] in {'API:POST /api-alias','API:POST /router-alias'} and edge[2]=='CONSUMES_API' for edge in facts),facts)
    def test_q1_a_api_router_alias_exposes_api(self):
        facts=self.indexed_facts('q1a',{'routes.ts':'const api = express.Router();\napi.post("/api-alias");\n'})
        self.assertIn(('SYMBOL:routes.api','API:POST /api-alias','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:routes.api','API:POST /api-alias','CONSUMES_API','lexical'),facts)
    def test_q1_b_handlers_router_alias_exposes_api(self):
        facts=self.indexed_facts('q1b',{'routes.ts':'const handlers = express.Router();\nhandlers.post("/router-alias");\n'})
        self.assertIn(('SYMBOL:routes.handlers','API:POST /router-alias','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:routes.handlers','API:POST /router-alias','CONSUMES_API','lexical'),facts)
    def test_q1_c_imported_api_client_consumes_api(self):
        facts=self.indexed_facts('q1c',{'client.ts':"import api from './api';\nexport function loadImported() { return api.post('/imported-api'); }\n"})
        self.assertIn(('SYMBOL:client.loadImported','API:POST /imported-api','CONSUMES_API','lexical'),facts)
    def test_q1_d_axios_factory_api_client_consumes_api(self):
        facts=self.indexed_facts('q1d',{'factory.ts':"const api = axios.create();\nexport function loadInvoice() { return api.post('/invoice'); }\n"})
        self.assertIn(('SYMBOL:factory.loadInvoice','API:POST /invoice','CONSUMES_API','lexical'),facts)
    def test_q1_e_loose_api_client_consumes_api(self):
        facts=self.indexed_facts('q1e',{'loose.ts':"export function loadLoose() { return api.post('/loose'); }\n"})
        self.assertIn(('SYMBOL:loose.loadLoose','API:POST /loose','CONSUMES_API','lexical'),facts)
    def test_q2_a_typed_api_client_router_alias_exposes_api(self):
        facts=self.indexed_facts('q2a',{'routes.ts':'const apiClient: Router = express.Router();\napiClient.post("/typed");\n'})
        self.assertIn(('SYMBOL:routes.apiClient','API:POST /typed','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:routes.apiClient','API:POST /typed','CONSUMES_API','lexical'),facts)
    def test_q2_b_typed_http_host_router_alias_exposes_api(self):
        facts=self.indexed_facts('q2b',{'routes.ts':'const httpHost: Router = express.Router();\nhttpHost.post("/typed2");\n'})
        self.assertIn(('SYMBOL:routes.httpHost','API:POST /typed2','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:routes.httpHost','API:POST /typed2','CONSUMES_API','lexical'),facts)
    def test_q2_c_imported_exported_router_alias_exposes_api(self):
        facts=self.indexed_facts('q2c',{
            'host.ts':'export const apiClient = express.Router();\n',
            'consumer.ts':"import { apiClient } from './host';\nexport function register() { return apiClient.post('/cross-file'); }\n",
        })
        self.assertIn(('SYMBOL:consumer.register','API:POST /cross-file','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:consumer.register','API:POST /cross-file','CONSUMES_API','lexical'),facts)
    def test_s2_parent_relative_named_router_alias_exposes_api(self):
        facts=self.indexed_facts('s2-parent',{
            'host.ts':'export const apiClient = express.Router();\n',
            'web/consumer.ts':"import { apiClient } from '../host';\nexport function register() { return apiClient.post('/parent-host'); }\n",
        })
        self.assertIn(('SYMBOL:web.consumer.register','API:POST /parent-host','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:web.consumer.register','API:POST /parent-host','CONSUMES_API','lexical'),facts)
    def test_s2_grandparent_relative_named_router_alias_exposes_api(self):
        facts=self.indexed_facts('s2-grandparent',{
            'host.ts':'export const apiClient = express.Router();\n',
            'web/nested/consumer.ts':"import { apiClient } from '../../host';\nexport function register() { return apiClient.post('/grandparent-host'); }\n",
        })
        self.assertIn(('SYMBOL:web.nested.consumer.register','API:POST /grandparent-host','EXPOSES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:web.nested.consumer.register','API:POST /grandparent-host','CONSUMES_API','lexical'),facts)
    def test_s2_parent_and_grandparent_source_suffix_named_router_aliases_expose_api(self):
        facts=self.indexed_facts('s2-source-suffix',{
            'host.ts':'export const apiClient = express.Router();\n',
            'web/consumer.ts':"import { apiClient } from '../host.ts';\nexport function parent() { return apiClient.post('/parent-suffix'); }\n",
            'web/nested/consumer.ts':"import { apiClient } from '../../host.ts';\nexport function grandparent() { return apiClient.post('/grandparent-suffix'); }\n",
        })
        for source,target in (
            ('SYMBOL:web.consumer.parent','API:POST /parent-suffix'),
            ('SYMBOL:web.nested.consumer.grandparent','API:POST /grandparent-suffix'),
        ):
            with self.subTest(source=source):
                self.assertIn((source,target,'EXPOSES_API','lexical'),facts)
                self.assertNotIn((source,target,'CONSUMES_API','lexical'),facts)
    def test_r1_a_named_import_client_name_collision_is_not_exposed(self):
        facts=self.indexed_facts('r1a',{
            'host.ts':'export const apiClient = express.Router();\n',
            'clientmod.ts':"import { apiClient } from './http-client';\nexport function fetchIt() { return apiClient.post('/real-client'); }\n",
        })
        self.assertIn(('SYMBOL:clientmod.fetchIt','API:POST /real-client','CONSUMES_API','lexical'),facts)
        self.assertNotIn(('SYMBOL:clientmod.fetchIt','API:POST /real-client','EXPOSES_API','lexical'),facts)
    def test_r1_b_default_import_client_name_collision_is_not_exposed(self):
        facts=self.indexed_facts('r1b',{
            'host.ts':'export const api = express.Router();\n',
            'clientdefault.ts':"import api from './client-api';\nexport function fetchIt() { return api.post('/collide'); }\n",
        })
        self.assertNotIn(('SYMBOL:clientdefault.fetchIt','API:POST /collide','EXPOSES_API','lexical'),facts)
    def test_r2_router_factory_forms_never_consume_api(self):
        facts=self.indexed_facts('r2',{
            'require.ts':'const api = require("express").Router();\napi.post("/require");\n',
            'assign.ts':'let api;\napi = express.Router();\napi.post("/assign");\n',
            'conditional.ts':'const api = cond ? express.Router() : express.Router();\napi.post("/conditional");\n',
            'http-host.ts':'const httpHost = require("express").Router();\nhttpHost.post("/http-host");\n',
            'api-client.ts':'const apiClient = require("express").Router();\napiClient.post("/api-client");\n',
        })
        forbidden={
            'API:POST /require','API:POST /assign','API:POST /conditional',
            'API:POST /http-host','API:POST /api-client',
        }
        expected={
            ('SYMBOL:require.api','API:POST /require','EXPOSES_API','lexical'),
            ('SYMBOL:assign.api','API:POST /assign','EXPOSES_API','lexical'),
            ('SYMBOL:conditional.api','API:POST /conditional','EXPOSES_API','lexical'),
            ('SYMBOL:http-host.httpHost','API:POST /http-host','EXPOSES_API','lexical'),
            ('SYMBOL:api-client.apiClient','API:POST /api-client','EXPOSES_API','lexical'),
        }
        self.assertTrue(expected <= facts,facts)
        self.assertFalse(any(edge[1] in forbidden and edge[2]=='CONSUMES_API' for edge in facts),facts)
    def test_q3_simulate_change_rejects_unknown_kind_and_avoids_self_loop(self):
        before=self.svc.g.export()
        with self.assertRaisesRegex(ValueError,'kind must be one of'):
            call(self.svc,'simulate_change',{'description':'unique simulation sentinel','kind':'UNDOCUMENTED'})
        self.assertEqual(self.svc.g.export(),before)
        result=call(self.svc,'simulate_change',{'description':'unique simulation sentinel','kind':'MODULE'})
        self.assertTrue(result['simulation'].startswith('MODULE:hypothetical:unique simulation sentinel'))
        self.assertFalse(any(edge['source']==result['simulation'] and edge['target']==result['simulation'] for edge in result['edges']),result)
        explicit=call(self.svc,'simulate_change',{'description':'explicit self safeguard','kind':'MODULE','targets':['MODULE:hypothetical:explicit self safeguard']})
        self.assertFalse(any(edge['source']==explicit['simulation'] and edge['target']==explicit['simulation'] for edge in explicit['edges']),explicit)
    def test_mcp_schema_and_call_match_cli_contract(self):
        schemas={tool['name']:tool['inputSchema'] for tool in tool_defs()}
        self.assertEqual(set(schemas),set(TOOLS))
        expected_properties={
            'project_map':set(), 'module_contract':{'module','depth'},
            'trace_flow':{'term','depth'}, 'trace_data':{'term','depth'}, 'trace_event':{'term','depth'},
            'requirement_impact':{'requirement','requirement_id'}, 'symbol_impact':{'symbol','depth'},
            'contract_impact':{'contract','depth'}, 'simulate_change':{'description','kind','targets'},
            'graph_diff':{'before','after'}, 'git_diff_impact':{'repo','rev','cochange'}, 'verify_spec':{'spec'},
        }
        self.assertEqual({name:set(schema['properties']) for name,schema in schemas.items()},expected_properties)
        self.assertEqual(schemas['module_contract']['required'],['module'])
        self.assertIn('term',schemas['trace_event']['properties'])
        self.assertFalse(schemas['module_contract']['additionalProperties'])
        result=call(self.svc,'module_contract',{'module':'src.orders','depth':2})
        self.assertEqual(result['module'],'src.orders')
        with self.assertRaisesRegex(ValueError,"requires argument\\(s\\): module"):
            call(self.svc,'module_contract',{})
        with self.assertRaisesRegex(ValueError,'does not accept'):
            call(self.svc,'module_contract',{'module':'src.orders','unexpected':True})
        with self.assertRaisesRegex(ValueError,'must be integer'):
            call(self.svc,'module_contract',{'module':'src.orders','depth':'2'})
        with self.assertRaisesRegex(ValueError,'must be at least 1'):
            call(self.svc,'module_contract',{'module':'src.orders','depth':0})
        parser=build_parser()
        subcommands=next(action.choices for action in parser._actions if hasattr(action,'choices') and action.choices)
        for name,schema in schemas.items():
            command=subcommands[name.replace('_','-')]
            cli_properties={action.dest for action in command._actions if action.dest not in {'help','db'}}
            self.assertEqual(cli_properties,set(schema['properties']),name)
            for required in schema.get('required',[]):
                action=next(action for action in command._actions if action.dest==required)
                self.assertTrue(action.required or action.nargs not in ('?','*'),f'{name}.{required} is optional in CLI')
                with self.assertRaisesRegex(ValueError,'requires argument'):
                    call(self.svc,name,{})
                cli_args=[name.replace('_','-')]
                if name != 'graph_diff': cli_args.extend(['--db',str(self.db)])
                rejected=self.run_cli(*cli_args,expected=2)
                self.assertIn('required',rejected.stderr)
        for command,term in (('trace-flow','src.orders.create_order'),('trace-data','src.orders.create_order'),('trace-event','order.created')):
            rejected=self.run_cli(command,term,'--depth','0','--db',str(self.db),expected=2)
            self.assertIn('must be at least 1',rejected.stderr)
    def test_mcp_rejects_every_required_type_and_minimum_violation(self):
        valid_values={'string':'value','integer':2,'boolean':False,'array':['value'],'object':{'requirements':[]}}
        invalid_values={'string':2,'integer':'2','boolean':0,'array':'value','object':[]}
        for name,schema in TOOL_SCHEMAS.items():
            properties=schema['properties']
            for required in schema.get('required',[]):
                with self.subTest(name=name,required=required):
                    args={key:valid_values[definition['type']] for key,definition in properties.items() if key != required}
                    with self.assertRaisesRegex(ValueError,f'requires argument\\(s\\): {required}'):
                        call(self.svc,name,args)
            for key,definition in properties.items():
                with self.subTest(name=name,key=key,check='type'):
                    args={item:valid_values[item_definition['type']] for item,item_definition in properties.items()}
                    args[key]=invalid_values[definition['type']]
                    with self.assertRaisesRegex(ValueError,f"argument '{key}' must be {definition['type']}"):
                        call(self.svc,name,args)
                if 'minimum' in definition:
                    with self.subTest(name=name,key=key,check='minimum'):
                        args={item:valid_values[item_definition['type']] for item,item_definition in properties.items()}
                        args[key]=definition['minimum']-1
                        with self.assertRaisesRegex(ValueError,f"argument '{key}' must be at least {definition['minimum']}"):
                            call(self.svc,name,args)
    def test_service_method_signatures_match_mcp_schema(self):
        for name,schema in TOOL_SCHEMAS.items():
            method=getattr(AnalysisService,name)
            parameters=[parameter.name for parameter in inspect.signature(method).parameters.values() if parameter.name != 'self']
            self.assertEqual(parameters,list(schema['properties']),name)
            required=set(schema.get('required',[]))
            for parameter in inspect.signature(method).parameters.values():
                if parameter.name == 'self':
                    continue
                self.assertEqual(parameter.default is inspect.Parameter.empty,parameter.name in required,f'{name}.{parameter.name}')
    def test_design_vocabulary_has_real_emission_points(self):
        design=(PROJECT/'DESIGN.md').read_text(encoding='utf-8')
        self.assertIn('### Emission inventory',design)
        source='\n'.join((PROJECT/'src'/'archgraph'/name).read_text(encoding='utf-8') for name in ('indexer.py','service.py'))
        for token in ('MODULE','FILE','SYMBOL','API','EVENT','SCHEMA','REQUIREMENT','TASK','CONTAINS','IMPORTS','CALLS','READS','WRITES','DATA_FLOWS_TO','EVENT_FLOWS_TO','EXPOSES_API','CONSUMES_API','PUBLISHES','SUBSCRIBES','USES_SCHEMA','IMPLEMENTS_REQUIREMENT','DEPENDS_ON','CO_CHANGES_WITH','ast','lexical','contract_adapter','requirement_match','simulation','git'):
            with self.subTest(token=token):
                self.assertIn(f'`{token}`',design)
                self.assertIn(repr(token),source)

if __name__=='__main__':unittest.main()
