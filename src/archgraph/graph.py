from __future__ import annotations

import json
import sqlite3
from collections import deque
from pathlib import Path
from typing import Iterable

from .model import Edge, Node


class GraphStore:
    """A deliberately small property graph stored in one portable SQLite file."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
          qualified_name TEXT, file_path TEXT, attrs TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS edges (
          source TEXT NOT NULL, target TEXT NOT NULL, type TEXT NOT NULL,
          confidence REAL NOT NULL, provenance TEXT NOT NULL, evidence TEXT NOT NULL,
          commit_sha TEXT, attrs TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY(source,target,type,provenance,evidence));
        CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
        CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
        CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
        """)
        self.db.commit()

    def close(self) -> None: self.db.close()
    def clear(self) -> None:
        self.db.executescript("DELETE FROM edges; DELETE FROM nodes;"); self.db.commit()
    def upsert_node(self, node: Node) -> None:
        self.db.execute("""INSERT INTO nodes VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
          kind=excluded.kind,name=excluded.name,qualified_name=excluded.qualified_name,
          file_path=excluded.file_path,attrs=excluded.attrs""",
          (node.id,node.kind,node.name,node.qualified_name,node.file_path,json.dumps(node.attrs,sort_keys=True)))
    def upsert_edge(self, edge: Edge) -> None:
        self.db.execute("""INSERT INTO edges VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source,target,type,provenance,evidence)
          DO UPDATE SET confidence=MAX(confidence,excluded.confidence), commit_sha=COALESCE(excluded.commit_sha,commit_sha), attrs=excluded.attrs""",
          (edge.source,edge.target,edge.type,edge.confidence,edge.provenance,edge.evidence,edge.commit,json.dumps(edge.attrs,sort_keys=True)))
    def commit(self) -> None: self.db.commit()
    def node(self, node_id: str) -> Node | None:
        row=self.db.execute("SELECT * FROM nodes WHERE id=?",(node_id,)).fetchone(); return self._node(row) if row else None
    def find(self, text: str, kinds: Iterable[str] | None = None) -> list[Node]:
        sql="SELECT * FROM nodes WHERE (id LIKE ? OR name LIKE ? OR qualified_name LIKE ? OR file_path LIKE ?)"; args=[f"%{text}%"]*4
        if kinds:
            marks=','.join('?' for _ in kinds); sql+=f" AND kind IN ({marks})"; args.extend(kinds)
        return [self._node(r) for r in self.db.execute(sql,args).fetchall()]
    def edges(self, node_id: str | None = None, direction: str = "both", types: Iterable[str] | None = None) -> list[Edge]:
        sql="SELECT * FROM edges"; conditions=[]; args=[]
        if node_id:
            if direction == "out": conditions.append("source=?"); args.append(node_id)
            elif direction == "in": conditions.append("target=?"); args.append(node_id)
            else: conditions.append("(source=? OR target=?)"); args.extend([node_id,node_id])
        if types:
            marks=','.join('?' for _ in types); conditions.append(f"type IN ({marks})"); args.extend(types)
        if conditions: sql += " WHERE " + " AND ".join(conditions)
        return [self._edge(r) for r in self.db.execute(sql,args).fetchall()]
    def neighborhood(self, starts: list[str], depth: int = 2, types: Iterable[str] | None = None) -> tuple[list[Node],list[Edge]]:
        seen=set(starts); frontier=deque((x,0) for x in starts); found=[]
        while frontier:
            item,d=frontier.popleft()
            if d >= depth: continue
            for e in self.edges(item,"both",types):
                found.append(e); other=e.target if e.source == item else e.source
                if other not in seen: seen.add(other); frontier.append((other,d+1))
        return [n for x in seen if (n:=self.node(x))], self._unique_edges(found)
    def reverse_impact(self, starts: list[str], depth: int = 4) -> tuple[list[Node],list[Edge]]:
        seen=set(starts); q=deque((x,0) for x in starts); found=[]
        while q:
            item,d=q.popleft()
            if d>=depth: continue
            for e in self.edges(item,"in"):
                found.append(e)
                if e.source not in seen: seen.add(e.source); q.append((e.source,d+1))
        return [n for x in seen if (n:=self.node(x))], self._unique_edges(found)
    def export(self) -> dict:
        nodes=[self._node(r).json() for r in self.db.execute("SELECT * FROM nodes")]
        edges=[self._edge(r).json() for r in self.db.execute("SELECT * FROM edges")]
        return {"nodes":nodes,"edges":edges}
    def _node(self,r:sqlite3.Row)->Node: return Node(r['id'],r['kind'],r['name'],r['qualified_name'] or '',r['file_path'] or '',json.loads(r['attrs']))
    def _edge(self,r:sqlite3.Row)->Edge: return Edge(r['source'],r['target'],r['type'],r['confidence'],r['provenance'],r['evidence'],r['commit_sha'],json.loads(r['attrs']))
    def _unique_edges(self,edges:list[Edge])->list[Edge]: return list({(e.source,e.target,e.type,e.provenance,e.evidence):e for e in edges}.values())
