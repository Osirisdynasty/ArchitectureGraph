"""Print direct GraphStore query results for the frozen Q1/Q2/Q3 fixtures."""
from __future__ import annotations

import tempfile
from pathlib import Path

from archgraph.graph import GraphStore
from archgraph.indexer import Indexer
from archgraph.service import AnalysisService


FIXTURES = {
    'Q1_A': {'routes.ts':'const api = express.Router();\napi.post("/api-alias");\n'},
    'Q1_B': {'routes.ts':'const handlers = express.Router();\nhandlers.post("/router-alias");\n'},
    'Q1_C': {'client.ts':"import api from './api';\nexport function loadImported() { return api.post('/imported-api'); }\n"},
    'Q1_D': {'factory.ts':"const api = axios.create();\nexport function loadInvoice() { return api.post('/invoice'); }\n"},
    'Q1_E': {'loose.ts':"export function loadLoose() { return api.post('/loose'); }\n"},
    'Q2_A': {'routes.ts':'const apiClient: Router = express.Router();\napiClient.post("/typed");\n'},
    'Q2_B': {'routes.ts':'const httpHost: Router = express.Router();\nhttpHost.post("/typed2");\n'},
    'Q2_C': {
        'host.ts':'export const apiClient = express.Router();\n',
        'consumer.ts':"import { apiClient } from './host';\nexport function register() { return apiClient.post('/cross-file'); }\n",
    },
}


def main() -> None:
    with tempfile.TemporaryDirectory(prefix='archgraph-q1-q3-query-') as directory:
        root=Path(directory)
        for label, files in FIXTURES.items():
            fixture=root/label; fixture.mkdir()
            for name, content in files.items(): (fixture/name).write_text(content,encoding='utf-8')
            db=root/f'{label}.db'; graph=GraphStore(db); Indexer(graph,fixture).index()
            for edge in graph.edges(types=['EXPOSES_API','CONSUMES_API']):
                source=graph.node(edge.source)
                print(f'{label} language={source.attrs.get("language")} {edge.source} --{edge.type}--> {edge.target} provenance={edge.provenance}')
            graph.close()
        db=root/'q3.db'; graph=GraphStore(db); graph.close(); service=AnalysisService(db)
        try:
            service.simulate_change('unique simulation sentinel','UNDOCUMENTED')
        except ValueError as exc:
            print(f'Q3_invalid_kind={type(exc).__name__}: {exc}')
        inferred=service.simulate_change('unique simulation sentinel','MODULE')
        explicit=service.simulate_change('explicit self safeguard','MODULE',['MODULE:hypothetical:explicit self safeguard'])
        print(f'Q3_inferred_self_loops={sum(e["source"]==inferred["simulation"] and e["target"]==inferred["simulation"] for e in inferred["edges"])}')
        print(f'Q3_explicit_self_loops={sum(e["source"]==explicit["simulation"] and e["target"]==explicit["simulation"] for e in explicit["edges"])}')
        service.close()


if __name__ == '__main__':
    main()
