"""Procurement rules, and a record that shows if anyone altered it.

Each audit line includes a hash of the line before it, forming a chain. Changing or removing an
old line breaks every later hash, so tampering can be detected, though not prevented.
"""
import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class Criterion:
    name: str
    weight: float
    kind: str = "score"        # "price" scores lowest as best, "score" is marked by the panel


@dataclass
class Bid:
    vendor: str
    price: float
    scores: dict = field(default_factory=dict)     # {criterion: {member: mark from 0 to 100}}
    compliant: bool = True
    delivery_days: int = 0


THRESHOLDS = [(25000, "direct purchase", 1), (250000, "request for quotations", 3), (float("inf"), "open tender", 3)]


def method_for(amount, thresholds=THRESHOLDS):
    """The buying method for a value, and the fewest quotes it needs. Figures are placeholders:
    use your organisation's procurement policy."""
    for limit, name, quotes in thresholds:
        if amount <= limit:
            return name, quotes
    return thresholds[-1][1], thresholds[-1][2]


def check_weights(criteria):
    total = sum(c.weight for c in criteria)
    if abs(total - 100) > 1e-9:
        raise ValueError(f"criteria weights add up to {total:g}, not 100")
    if not any(c.kind == "price" for c in criteria):
        raise ValueError("a price criterion is needed")


def evaluate(bids, criteria):
    """Weighted scoring. Price points are lowest price / this price × 100. Panel marks are averaged.
    Non-compliant bids are shown but cannot win."""
    check_weights(criteria)
    valid = [b for b in bids if b.compliant]
    lowest = min((b.price for b in valid), default=None)
    rows = []
    for b in bids:
        detail, total = {}, 0.0
        for c in criteria:
            if c.kind == "price":
                pts = 100 * lowest / b.price if b.compliant and lowest and b.price > 0 else 0.0
            else:
                marks = list(b.scores.get(c.name, {}).values())
                pts = sum(marks) / len(marks) if marks else 0.0
            detail[c.name] = pts
            total += pts * c.weight / 100
        rows.append({"vendor": b.vendor, "price": b.price, "compliant": b.compliant, "detail": detail, "total": total if b.compliant else None})
    rows.sort(key=lambda r: (r["total"] is None, -(r["total"] or 0), r["price"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i if r["compliant"] else None
    return rows


def sensitivity(bids, criteria, swing=10):
    """Would the winner change if the weights moved a little? A close result deserves a closer look."""
    base = evaluate(bids, criteria)
    winner = base[0]["vendor"] if base and base[0]["compliant"] else None
    changes = []
    for i, c in enumerate(criteria):
        for j, d in enumerate(criteria):
            if i == j or c.weight < swing:
                continue
            moved = [Criterion(x.name, x.weight + (-swing if k == i else swing if k == j else 0), x.kind) for k, x in enumerate(criteria)]
            new = evaluate(bids, moved)[0]["vendor"]
            if new != winner:
                changes.append((c.name, d.name, new))
    return changes


def red_flags(bids, quotes_needed, members_conflicted=(), estimate=None):
    """Patterns that deserve a question. None of them proves wrongdoing."""
    out = []
    n = len(bids)
    if n < quotes_needed:
        out.append(f"only {n} quotes against the {quotes_needed} required")
    prices = sorted(b.price for b in bids)
    if len(prices) >= 3:
        spread = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0
        if spread < 0.01:
            out.append("prices are within 1% of each other, which can suggest coordination")
    if len(prices) >= 2 and prices[0] > 0 and prices[1] / prices[0] > 1.5:
        out.append("the lowest price is far below the next: check that it is realistic")
    if estimate and prices and prices[0] > estimate * 1.2:
        out.append("every price is more than 20% above the estimate")
    for m in members_conflicted:
        out.append(f"{m} declared a conflict of interest and must not score this")
    for b in bids:
        for c, marks in b.scores.items():
            if len(marks) >= 3:
                vals = sorted(marks.values())
                if vals[-1] - vals[0] >= 50:
                    out.append(f"panel marks for {b.vendor} on {c} differ by {vals[-1] - vals[0]:g}: discuss before finalising")
    return out


def award_text(request, rows, criteria):
    """A draft justification for the file."""
    if not rows or not rows[0]["compliant"]:
        return "No compliant bid was received."
    w = rows[0]
    others = ", ".join(f"{r['vendor']} ({r['total']:.1f})" for r in rows[1:] if r["compliant"])
    return f"For {request}, {len(rows)} bids were received and evaluated against {len(criteria)} weighted criteria. {w['vendor']} scored highest at {w['total']:.1f} of 100, at a price of {w['price']:,.2f}." + (f" Other compliant bids scored: {others}." if others else "")


GENESIS = "0" * 64


def link(previous, record):
    body = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((previous + body).encode()).hexdigest()


def append(chain, record):
    prev = chain[-1]["hash"] if chain else GENESIS
    chain.append({"record": record, "hash": link(prev, record)})
    return chain[-1]["hash"]


def verify_chain(chain):
    """Returns the first broken position, or None if the chain is intact."""
    prev = GENESIS
    for i, row in enumerate(chain):
        if link(prev, row["record"]) != row["hash"]:
            return i
        prev = row["hash"]
    return None
