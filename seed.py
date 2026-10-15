"""
seed.py - builds a synthetic fraud graph in FalkorDB.

Graph model
  (Account)-[:USED]->(Device)
  (Account)-[:MADE]->(Transaction)-[:TO]->(Merchant)

Hidden inside the normal data are 3 fraud rings:
  - several accounts share the same device(s)
  - they all send large payments to the same shady merchant
  - one account (the "bridge") links ring 0 and ring 1 together
One transaction in each ring is flagged = true (this is what the agent
will be asked to investigate).

Run:  python seed.py
"""

import random

from faker import Faker
from falkordb import FalkorDB

random.seed(42)
Faker.seed(42)
fake = Faker()

GRAPH_NAME = "fraud"

# ---------------------------------------------------------------- connect
db = FalkorDB(host="localhost", port=6379)

# start from a clean graph every time you run this script
try:
    db.select_graph(GRAPH_NAME).delete()
except Exception:
    pass  # graph did not exist yet, that's fine

g = db.select_graph(GRAPH_NAME)

# indexes make lookups by id fast
for label in ["Account", "Device", "Merchant", "Transaction"]:
    try:
        g.query(f"CREATE INDEX FOR (n:{label}) ON (n.id)")
    except Exception as e:
        print(f"(index on {label} skipped: {e})")

# ------------------------------------------------------- data containers
accounts = []   # {id, name, age_days}
devices = []    # {id}
merchants = []  # {id, name, category}
used = []       # {a: account_id, d: device_id}
txns = []       # {id, a, m, amount, flagged, day}

txn_counter = 0


def add_txn(account_id, merchant_id, amount, flagged=False):
    global txn_counter
    txn_counter += 1
    txns.append(
        {
            "id": f"T{txn_counter}",
            "a": account_id,
            "m": merchant_id,
            "amount": round(amount, 2),
            "flagged": flagged,
            "day": random.randint(1, 30),  # day within the last 30 days
        }
    )
    return f"T{txn_counter}"


# ----------------------------------------------------- 1) normal merchants
categories = ["grocery", "fuel", "electronics", "restaurant", "travel", "pharmacy"]
for i in range(30):
    merchants.append(
        {"id": f"M{i}", "name": fake.company(), "category": random.choice(categories)}
    )

# ------------------------------------------------------ 2) normal accounts
NORMAL_ACCOUNTS = 200
for i in range(NORMAL_ACCOUNTS):
    accounts.append(
        {"id": f"A{i}", "name": fake.name(), "age_days": random.randint(200, 3000)}
    )
    devices.append({"id": f"D{i}"})
    used.append({"a": f"A{i}", "d": f"D{i}"})

# a few innocent shared devices (e.g. family members) so not every shared
# device means fraud. This makes the problem more realistic.
for i in range(0, 10, 2):
    used.append({"a": f"A{i + 1}", "d": f"D{i}"})

# ------------------------------------------------- 3) normal transactions
for _ in range(1000):
    acc = f"A{random.randint(0, NORMAL_ACCOUNTS - 1)}"
    mer = f"M{random.randint(0, len(merchants) - 1)}"
    add_txn(acc, mer, random.uniform(5, 500))

# --------------------------------------------------------- 4) fraud rings
NUM_RINGS = 3
RING_SIZE = 5
flagged_ids = []

for r in range(NUM_RINGS):
    # shady merchant and two shared devices for the ring
    shady = f"MS{r}"
    merchants.append(
        {"id": shady, "name": f"{fake.company()} (shell)", "category": "electronics"}
    )
    dev_a, dev_b = f"DR{r}_1", f"DR{r}_2"
    devices.append({"id": dev_a})
    devices.append({"id": dev_b})

    ring_txns = []
    for k in range(RING_SIZE):
        acc_id = f"R{r}_{k}"
        # ring accounts are young
        accounts.append(
            {"id": acc_id, "name": fake.name(), "age_days": random.randint(3, 40)}
        )
        # every ring account uses one shared device, some use both
        used.append({"a": acc_id, "d": dev_a if k % 2 == 0 else dev_b})
        if k == 0:
            used.append({"a": acc_id, "d": dev_b})

        # large payments to the shady merchant
        for _ in range(4):
            t = add_txn(acc_id, shady, random.uniform(900, 4000))
            ring_txns.append(t)

        # a couple of ordinary payments so they blend in
        for _ in range(2):
            add_txn(acc_id, f"M{random.randint(0, 29)}", random.uniform(10, 120))

    # flag one suspicious transaction per ring: this is the agent's starting point
    flag_id = ring_txns[0]
    for t in txns:
        if t["id"] == flag_id:
            t["flagged"] = True
            t["amount"] = round(random.uniform(4500, 6000), 2)
    flagged_ids.append(flag_id)

# bridge account links ring 0 and ring 1 through their shared devices
accounts.append({"id": "BRIDGE", "name": fake.name(), "age_days": 15})
used.append({"a": "BRIDGE", "d": "DR0_2"})
used.append({"a": "BRIDGE", "d": "DR1_1"})
add_txn("BRIDGE", "MS0", 2500)
add_txn("BRIDGE", "MS1", 2700)

# ---------------------------------------------------------------- load
print("Loading into FalkorDB...")

g.query(
    "UNWIND $rows AS r CREATE (:Account {id: r.id, name: r.name, age_days: r.age_days})",
    {"rows": accounts},
)
g.query("UNWIND $rows AS r CREATE (:Device {id: r.id})", {"rows": devices})
g.query(
    "UNWIND $rows AS r CREATE (:Merchant {id: r.id, name: r.name, category: r.category})",
    {"rows": merchants},
)
g.query(
    """
    UNWIND $rows AS r
    MATCH (a:Account {id: r.a}), (d:Device {id: r.d})
    CREATE (a)-[:USED]->(d)
    """,
    {"rows": used},
)
g.query(
    """
    UNWIND $rows AS r
    MATCH (a:Account {id: r.a}), (m:Merchant {id: r.m})
    CREATE (a)-[:MADE]->(:Transaction {id: r.id, amount: r.amount,
                                       flagged: r.flagged, day: r.day})-[:TO]->(m)
    """,
    {"rows": txns},
)

# ------------------------------------------------------------- verify
def count(q):
    return g.query(q).result_set[0][0]


print("\nDone! Graph:", GRAPH_NAME)
print("  Accounts     :", count("MATCH (n:Account) RETURN count(n)"))
print("  Devices      :", count("MATCH (n:Device) RETURN count(n)"))
print("  Merchants    :", count("MATCH (n:Merchant) RETURN count(n)"))
print("  Transactions :", count("MATCH (n:Transaction) RETURN count(n)"))
print("  Relationships:", count("MATCH ()-[r]->() RETURN count(r)"))
print("  Flagged txns :", flagged_ids)

print("\nSanity check - accounts sharing a device with R0_1:")
res = g.query(
    """
    MATCH (a:Account {id: 'R0_1'})-[:USED]->(d:Device)<-[:USED]-(other:Account)
    WHERE other <> a
    RETURN DISTINCT other.id
    """
)
print("  ", [row[0] for row in res.result_set])
