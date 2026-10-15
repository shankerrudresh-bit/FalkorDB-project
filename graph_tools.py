"""
graph_tools.py - the tools your agent will use to investigate fraud.

Every function returns a dict with:
  - "data":   the answer
  - "cypher": the query that produced it  (this powers 'explainable answers')

Run `python graph_tools.py` to test all tools by hand.
"""

from falkordb import FalkorDB

GRAPH_NAME = "fraud"

_db = FalkorDB(host="localhost", port=6379)
_g = _db.select_graph(GRAPH_NAME)


def _run(cypher, params=None):
    """Run a query and return (rows, cypher_text)."""
    res = _g.query(cypher, params or {})
    return res.result_set, " ".join(cypher.split())


# ------------------------------------------------------------------ tools
def get_flagged_transactions():
    """List all transactions that were flagged as suspicious."""
    q = """
    MATCH (a:Account)-[:MADE]->(t:Transaction {flagged: true})-[:TO]->(m:Merchant)
    RETURN t.id, t.amount, a.id, m.id, m.name
    ORDER BY t.amount DESC
    """
    rows, cypher = _run(q)
    data = [
        {"transaction": r[0], "amount": r[1], "account": r[2],
         "merchant_id": r[3], "merchant_name": r[4]}
        for r in rows
    ]
    return {"data": data, "cypher": cypher}


def get_account_profile(account_id):
    """Basic facts about an account: age, devices used, number of transactions."""
    q = """
    MATCH (a:Account {id: $id})
    OPTIONAL MATCH (a)-[:USED]->(d:Device)
    WITH a, collect(DISTINCT d.id) AS devices
    OPTIONAL MATCH (a)-[:MADE]->(t:Transaction)
    RETURN a.name, a.age_days, devices, count(t), coalesce(sum(t.amount), 0)
    """
    rows, cypher = _run(q, {"id": account_id})
    if not rows:
        return {"data": None, "cypher": cypher}
    r = rows[0]
    return {
        "data": {"account": account_id, "name": r[0], "age_days": r[1],
                 "devices": r[2], "txn_count": r[3], "total_sent": round(r[4], 2)},
        "cypher": cypher,
    }


def find_linked_accounts(account_id, max_hops=2):
    """
    Accounts connected to this one through shared devices.
    1 hop  = shares a device directly.
    2 hops = shares a device with someone who shares a device with you, etc.
    """
    max_hops = max(1, min(int(max_hops), 4))
    # a hop is Account->Device->Account, i.e. 2 edges
    q = f"""
    MATCH p = (a:Account {{id: $id}})-[:USED*2..{2 * max_hops}]-(o:Account)
    WHERE o <> a
    RETURN o.id AS account, min(length(p)) / 2 AS hops
    ORDER BY hops, account
    """
    rows, cypher = _run(q, {"id": account_id})
    data = [{"account": r[0], "hops": int(r[1])} for r in rows]
    return {"data": data, "cypher": cypher}


def shortest_path(account_a, account_b):
    """The shortest chain of connections between two accounts."""
    params = {"a": account_a, "b": account_b}
    q = """
    MATCH (a:Account {id: $a}), (b:Account {id: $b})
    MATCH p = shortestPath((a)-[:USED*..10]-(b))
    RETURN [n IN nodes(p) | n.id] AS path
    """
    try:
        rows, cypher = _run(q, params)
    except Exception:
        # fallback if shortestPath syntax differs on your version
        q = """
        MATCH (a:Account {id: $a}), (b:Account {id: $b})
        MATCH p = (a)-[:USED*..10]-(b)
        RETURN [n IN nodes(p) | n.id] AS path
        ORDER BY length(p) LIMIT 1
        """
        rows, cypher = _run(q, params)
    path = rows[0][0] if rows else None
    return {"data": {"path": path}, "cypher": cypher}


def get_merchant_exposure(merchant_id):
    """Who paid this merchant, how many times, and how much in total."""
    q = """
    MATCH (a:Account)-[:MADE]->(t:Transaction)-[:TO]->(m:Merchant {id: $id})
    RETURN a.id AS account, count(t) AS txns, sum(t.amount) AS total
    ORDER BY total DESC LIMIT 20
    """
    rows, cypher = _run(q, {"id": merchant_id})
    data = [{"account": r[0], "txns": r[1], "total": round(r[2], 2)} for r in rows]
    return {"data": data, "cypher": cypher}


def get_account_risk(account_id):
    """
    Simple, explainable risk score from graph signals:
    young account + many linked accounts + large payments.
    """
    q = """
    MATCH (a:Account {id: $id})
    OPTIONAL MATCH (a)-[:USED]->(:Device)<-[:USED]-(o:Account)
    WHERE o <> a
    WITH a, count(DISTINCT o) AS linked
    OPTIONAL MATCH (a)-[:MADE]->(t:Transaction)
    RETURN a.age_days, linked, count(t), coalesce(max(t.amount), 0)
    """
    rows, cypher = _run(q, {"id": account_id})
    if not rows:
        return {"data": None, "cypher": cypher}
    age, linked, txns, biggest = rows[0]

    score, reasons = 0, []
    if age < 60:
        score += 30
        reasons.append(f"account is only {age} days old")
    if linked >= 3:
        score += 40
        reasons.append(f"shares devices with {linked} other accounts")
    elif linked >= 1:
        score += 10
        reasons.append(f"shares a device with {linked} other account(s)")
    if biggest > 2000:
        score += 30
        reasons.append(f"largest payment is {round(biggest, 2)}")

    level = "HIGH" if score >= 70 else "MEDIUM" if score >= 30 else "LOW"
    return {
        "data": {"account": account_id, "risk_score": score,
                 "risk_level": level, "reasons": reasons},
        "cypher": cypher,
    }


# ------------------------------------------------------------ manual test
# ---------------------------------------------------------------- actions
def freeze_account(account_id, reason):
    """ACTION: mark an account as frozen in the graph, with the reason."""
    q = """
    MATCH (a:Account {id: $id})
    SET a.frozen = true, a.freeze_reason = $reason
    RETURN a.id
    """
    rows, cypher = _run(q, {"id": account_id, "reason": reason})
    return {"data": {"frozen": bool(rows), "account": account_id, "reason": reason},
            "cypher": cypher}


def get_frozen_accounts():
    """List all accounts that are currently frozen."""
    q = "MATCH (a:Account {frozen: true}) RETURN a.id, a.freeze_reason ORDER BY a.id"
    rows, cypher = _run(q)
    data = [{"account": r[0], "reason": r[1]} for r in rows]
    return {"data": data, "cypher": cypher}


if __name__ == "__main__":
    import json

    def show(title, result):
        print(f"\n=== {title} ===")
        print(json.dumps(result["data"], indent=2))

    show("Flagged transactions", get_flagged_transactions())
    show("Profile of R0_1", get_account_profile("R0_1"))
    show("Linked to R0_1 (2 hops)", find_linked_accounts("R0_1", 2))
    show("Path R0_1 -> R1_2", shortest_path("R0_1", "R1_2"))
    show("Who paid shell merchant MS0", get_merchant_exposure("MS0"))
    show("Risk of R0_1", get_account_risk("R0_1"))
    show("Risk of a normal account A5", get_account_risk("A5"))
