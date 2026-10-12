"""Mock entity-relationship graph service (stands in for the GERNAS KG)."""
from __future__ import annotations

from typing import Any

from src.mock_services.data_store import cite, load, not_found

SERVICE = "graph"


def get_entity_links(entity_id: str, depth: int = 2) -> dict[str, Any]:
    """Breadth-first links around an entity, up to ``depth`` hops (max 3)."""
    g = load("graph")
    if entity_id not in g["entities"]:
        return not_found("get_entity_links", "entity_id", entity_id)
    depth = max(1, min(int(depth), 3))
    frontier, seen, edges = {entity_id}, {entity_id}, []
    for _ in range(depth):
        nxt: set[str] = set()
        for e in g["edges"]:
            if (e["source"] in frontier or e["target"] in frontier) and e not in edges:
                edges.append(e)
                nxt |= {e["source"], e["target"]} - seen
        seen |= nxt
        frontier = nxt
    nodes = {n: g["entities"].get(n, {"label": n}) for n in seen}
    return {"tool": "get_entity_links", "found": True, "entity_id": entity_id, "depth": depth,
            "nodes": nodes, "edges": edges, "citations": [cite("get_entity_links", e) for e in edges]}


def find_shared_attributes(entity_id: str) -> dict[str, Any]:
    """Shared address / director / ownership links, which often indicate a hidden network."""
    g = load("graph")
    if entity_id not in g["entities"]:
        return not_found("find_shared_attributes", "entity_id", entity_id)
    shared_rel = {"shares_registered_address", "director_of", "beneficial_owner_of", "close_family_member_of"}
    one_hop = [e for e in g["edges"] if entity_id in (e["source"], e["target"])]
    neighbours = {e["source"] for e in one_hop} | {e["target"] for e in one_hop}
    edges = [e for e in g["edges"] if e["relation"] in shared_rel
             and (e["source"] in neighbours or e["target"] in neighbours)]
    return {"tool": "find_shared_attributes", "found": True, "entity_id": entity_id, "links": edges,
            "citations": [cite("find_shared_attributes", e) for e in edges]}


TOOLS = [get_entity_links, find_shared_attributes]
