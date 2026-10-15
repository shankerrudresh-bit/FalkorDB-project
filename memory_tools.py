"""
memory_tools.py - persistent agent memory stored in FalkorDB.

Each analyst gets their OWN memory graph (memory_alice, memory_bob, ...),
so memories are isolated per user. Choose the analyst with an env var:

    $env:ANALYST="alice"      (default)

Memory model
  (Case)-[:INVOLVES]->(Entity)     past investigations and who/what they touched
  (Lesson)                         general rules the agent learned, with a use counter
"""

import os
import re
import time

from falkordb import FalkorDB

_db = FalkorDB(host="localhost", port=6379)


def _analyst():
    name = os.getenv("ANALYST", "alice")
    return re.sub(r"\W", "", name) or "alice"


def _mem():
    name = _analyst()
    return _db.select_graph(f"memory_{name}"), name


def save_case(summary, verdict, entity_ids):
    """Store a finished investigation and link it to every account/merchant involved."""
    g, name = _mem()
    case_id = f"C{int(time.time())}"
    g.query(
        "CREATE (:Case {id: $id, summary: $s, verdict: $v, created: $t})",
        {"id": case_id, "s": summary, "v": verdict, "t": int(time.time())},
    )
    g.query(
        """
        MATCH (c:Case {id: $id})
        UNWIND $ents AS e
        MERGE (n:Entity {id: e})
        CREATE (c)-[:INVOLVES]->(n)
        """,
        {"id": case_id, "ents": list(entity_ids)},
    )
    return {
        "data": {"saved_case": case_id, "analyst": name, "entities": len(entity_ids)},
        "cypher": "CREATE (:Case ...) + (Case)-[:INVOLVES]->(Entity)",
    }


def recall_similar_cases(entity_ids):
    """Find past cases that involved any of these accounts/merchants (most overlap first)."""
    g, name = _mem()
    q = """
    UNWIND $ents AS e
    MATCH (c:Case)-[:INVOLVES]->(:Entity {id: e})
    WITH c, count(*) AS overlap
    RETURN c.id, c.summary, c.verdict, c.created, overlap
    ORDER BY overlap DESC LIMIT 5
    """
    try:
        rows = g.query(q, {"ents": list(entity_ids)}).result_set
    except Exception:
        rows = []
    data = [
        {"case": r[0], "summary": r[1], "verdict": r[2], "created": r[3],
         "overlap": r[4]}
        for r in rows
    ]
    return {"data": {"analyst": name, "similar_cases": data}, "cypher": " ".join(q.split())}


def save_lesson(rule):
    """Remember a general rule. If it already exists, increase its use counter."""
    g, name = _mem()
    g.query(
        """
        MERGE (l:Lesson {rule: $r})
        ON CREATE SET l.uses = 1, l.created = $t
        ON MATCH SET l.uses = l.uses + 1
        """,
        {"r": rule, "t": int(time.time())},
    )
    return {"data": {"saved_lesson": rule, "analyst": name},
            "cypher": "MERGE (l:Lesson {rule: $r}) ..."}


def get_lessons():
    """Return the most-used lessons this analyst's agent has learned."""
    g, name = _mem()
    q = "MATCH (l:Lesson) RETURN l.rule, l.uses ORDER BY l.uses DESC LIMIT 10"
    try:
        rows = g.query(q).result_set
    except Exception:
        rows = []
    data = [{"rule": r[0], "times_used": r[1]} for r in rows]
    return {"data": {"analyst": name, "lessons": data}, "cypher": q}
