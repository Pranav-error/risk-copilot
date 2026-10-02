"""Round-tripping / layering detection: money that leaves an account and comes back.

A cycle is a chain of transfers A->B->...->A where each hop happens after the
previous one, the whole chain fits in a time window, and the amount is roughly
conserved hop to hop (layering skims small fees, it doesn't change the size).

Idea from Arbix's graph-cycle detector, but Bellman-Ford ignores time order, so
it would report cycles no money could actually have travelled. This is a
bounded DFS over time-ordered edges instead.

Runs two ways:
  - locally:   python detection/cycles.py   (self-check on a toy graph)
  - Snowflake: registered as a Snowpark stored procedure (sql/10_cycle_proc.sql)
"""
import json
from collections import defaultdict
from datetime import timedelta

MAX_HOPS = 4                 # A->B->A (2) up to 4 hops; longer chains are rare and slow
WINDOW = timedelta(days=7)
MIN_AMOUNT = 10_000          # ignore small transfers, they aren't worth laundering
CONSERVATION = 0.80          # each hop must carry >= 80% of the previous hop


def find_cycles(txns, max_hops=MAX_HOPS, window=WINDOW,
                min_amount=MIN_AMOUNT, conservation=CONSERVATION):
    """txns: iterable of (txn_id, from_acct, to_acct, amount, ts).
    Returns a list of cycles, each a list of txn tuples in hop order."""
    out_edges = defaultdict(list)
    edges = []
    for t in txns:
        t = (t[0], t[1], t[2], float(t[3]), t[4])  # Snowflake NUMBER arrives as Decimal
        if t[2] is None or t[1] == t[2] or t[3] < min_amount:
            continue
        out_edges[t[1]].append(t)
        edges.append(t)
    for lst in out_edges.values():
        lst.sort(key=lambda t: t[4])

    # ponytail: DFS from every edge, O(E * d^hops); fine for ~100K txns with the
    # amount filter. If it gets slow, restrict start edges to high-risk accounts.
    found, seen = [], set()

    def dfs(path, visited):
        first, last = path[0], path[-1]
        for nxt in out_edges[last[2]]:
            if nxt[4] <= last[4]:
                continue
            if nxt[4] - first[4] > window:
                break  # sorted by ts, everything after is later still
            if nxt[3] < last[3] * conservation or nxt[3] > last[3]:
                continue
            if nxt[2] == first[1]:
                key = frozenset(t[0] for t in path) | {nxt[0]}
                if key not in seen:
                    seen.add(key)
                    found.append(path + [nxt])
            elif len(path) + 1 < max_hops and nxt[2] not in visited:
                dfs(path + [nxt], visited | {nxt[2]})

    for e in edges:
        dfs([e], {e[1], e[2]})
    return found


def to_alert(cycle):
    origin = cycle[0][1]
    first, last = cycle[0], cycle[-1]
    return {
        "RULE": "ROUND_TRIP_CYCLE",
        "ACCOUNT_ID": origin,
        "TXN_IDS": [t[0] for t in cycle],
        "SEVERITY": "HIGH" if len(cycle) >= 3 else "MEDIUM",
        "EVIDENCE": {
            "path": [t[1] for t in cycle] + [origin],
            "hops": len(cycle),
            "amount_out": float(first[3]),
            "amount_back": float(last[3]),
            "hours_elapsed": round((last[4] - first[4]).total_seconds() / 3600, 1),
            "policy": "5.2",
        },
    }


def run(session):
    """Snowpark stored-procedure handler. Rewrites this rule's rows in ALERTS."""
    rows = session.sql(
        "SELECT TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, AMOUNT, TXN_TS "
        "FROM TRANSACTIONS WHERE FROM_ACCOUNT_ID IS NOT NULL AND TO_ACCOUNT_ID IS NOT NULL "
        "AND AMOUNT >= ?",
        params=[MIN_AMOUNT],
    ).collect()
    cycles = find_cycles(tuple(r) for r in rows)
    session.sql("DELETE FROM ALERTS WHERE RULE = 'ROUND_TRIP_CYCLE'").collect()
    for a in map(to_alert, cycles):
        session.sql(
            "INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE) "
            "SELECT ?, ?, PARSE_JSON(?), ?, PARSE_JSON(?)",
            params=[a["RULE"], a["ACCOUNT_ID"], json.dumps(a["TXN_IDS"]),
                    a["SEVERITY"], json.dumps(a["EVIDENCE"])],
        ).collect()
    return f"{len(cycles)} round-trip cycles"


if __name__ == "__main__":
    from datetime import datetime
    d = lambda h: datetime(2026, 9, 1) + timedelta(hours=h)
    txns = [
        # real 3-hop cycle, amounts shrink by fees
        ("t1", "A", "B", 500_000, d(0)), ("t2", "B", "C", 480_000, d(5)), ("t3", "C", "A", 470_000, d(20)),
        # 2-hop round trip
        ("t4", "D", "E", 200_000, d(0)), ("t5", "E", "D", 195_000, d(30)),
        # time order broken: G->F happens before F->G, so money can't have returned
        ("t6", "F", "G", 300_000, d(10)), ("t7", "G", "F", 290_000, d(1)),
        # outside the 7-day window
        ("t8", "H", "I", 300_000, d(0)), ("t9", "I", "H", 290_000, d(24 * 8)),
        # amount not conserved (500k out, 50k back) -> ordinary payment, not a cycle
        ("t10", "J", "K", 500_000, d(0)), ("t11", "K", "J", 50_000, d(2)),
        # below MIN_AMOUNT
        ("t12", "L", "M", 5_000, d(0)), ("t13", "M", "L", 4_900, d(1)),
        # external counterparty (no internal account)
        ("t14", "N", None, 900_000, d(0)),
    ]
    got = sorted(sorted(t[0] for t in c) for c in find_cycles(txns))
    assert got == [["t1", "t2", "t3"], ["t4", "t5"]], got
    from decimal import Decimal
    as_snowflake = [(t[0], t[1], t[2], Decimal(str(t[3])), t[4]) for t in txns]
    assert sorted(sorted(t[0] for t in c) for c in find_cycles(as_snowflake)) == got
    a = to_alert(find_cycles(txns[:3])[0])
    assert a["EVIDENCE"]["path"] == ["A", "B", "C", "A"] and a["SEVERITY"] == "HIGH", a
    assert a["EVIDENCE"]["policy"] == "5.2", a  # every rule must name its clause, or the AI guesses
    print("ok", got)
