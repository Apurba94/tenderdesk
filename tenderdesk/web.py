"""The offline web app."""
import json
from datetime import datetime

from . import core
from .fieldkit import App, Fail, Raw, card, form, h, link, redirect, stats, table

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY, title TEXT NOT NULL, estimate REAL NOT NULL, criteria TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', winner TEXT);
CREATE TABLE IF NOT EXISTS bids (id INTEGER PRIMARY KEY, request INTEGER NOT NULL REFERENCES requests(id), vendor TEXT NOT NULL, price REAL NOT NULL, compliant INTEGER NOT NULL DEFAULT 1, UNIQUE (request, vendor));
CREATE TABLE IF NOT EXISTS marks (bid INTEGER NOT NULL REFERENCES bids(id), criterion TEXT NOT NULL, member TEXT NOT NULL, mark REAL NOT NULL, PRIMARY KEY (bid, criterion, member));
CREATE TABLE IF NOT EXISTS conflicts (request INTEGER NOT NULL REFERENCES requests(id), member TEXT NOT NULL, PRIMARY KEY (request, member));
CREATE TABLE IF NOT EXISTS chain (id INTEGER PRIMARY KEY, record TEXT NOT NULL, hash TEXT NOT NULL);
"""
DEFAULT = "Price, 50, price\nTechnical quality, 30\nDelivery time, 20"


def create_app(db="tender.db", clock=datetime.now):
    a = App("TenderDesk", db, SCHEMA, [("Requests", "/"), ("New request", "/new"), ("Audit trail", "/audit")])

    def record(event, **details):
        last = a.one("SELECT hash FROM chain ORDER BY id DESC LIMIT 1") or core.GENESIS
        rec = {"at": clock().isoformat(timespec="seconds"), "event": event, **details}
        a.run("INSERT INTO chain (record, hash) VALUES (?, ?)", json.dumps(rec, sort_keys=True, ensure_ascii=False), core.link(last, rec))

    def chain():
        return [{"record": json.loads(r["record"]), "hash": r["hash"]} for r in a.rows("SELECT * FROM chain ORDER BY id")]

    def load(rid):
        r = a.row("SELECT * FROM requests WHERE id = ?", rid)
        if r is None:
            raise Fail("No such request", 404)
        crit = [core.Criterion(*c) for c in json.loads(r["criteria"])]
        bids = []
        for b in a.rows("SELECT * FROM bids WHERE request = ? ORDER BY id", rid):
            scores = {}
            for m in a.rows("SELECT * FROM marks WHERE bid = ?", b["id"]):
                scores.setdefault(m["criterion"], {})[m["member"]] = m["mark"]
            bids.append(core.Bid(b["vendor"], b["price"], scores, bool(b["compliant"])))
        return r, crit, bids

    @a.route("/")
    def home(req):
        return a.page("Requests", card("Requests", table(a.rows("SELECT * FROM requests ORDER BY id DESC"), [("Request", lambda r: link(f"/request/{r['id']}", r["title"])), ("Estimate", lambda r: f"{r['estimate']:,.2f}"), ("Method", lambda r: core.method_for(r["estimate"])[0]), ("Status", "status"), ("Winner", "winner")], "No requests yet.")))

    @a.route("/new", ("GET", "POST"))
    def new(req):
        if req.method == "POST":
            crit = []
            for line in req.text("criteria").splitlines():
                p = [x.strip() for x in line.split(",")]
                if len(p) < 2:
                    raise Fail(f"Each criterion needs a name and a weight: {line}")
                try:
                    crit.append(core.Criterion(p[0], float(p[1]), p[2] if len(p) > 2 else "score"))
                except ValueError:
                    raise Fail(f"Not a number in: {line}") from None
            try:
                core.check_weights(crit)
            except ValueError as exc:
                raise Fail(str(exc)) from None
            cur = a.run("INSERT INTO requests (title, estimate, criteria) VALUES (?, ?, ?)", req.text("title"), req.number("estimate", low=0), json.dumps([[c.name, c.weight, c.kind] for c in crit], ensure_ascii=False))
            record("request created", request=cur.lastrowid, title=req.text("title"))
            return redirect(f"/request/{cur.lastrowid}")
        return a.page("New request", card("Purchase request", form(a, "/new", [("title", "What is being bought"), ("estimate", "Estimated value", "number"), ("criteria", "Criteria: name, weight (add ', price' for the price line)", "textarea", DEFAULT)], "Create")))

    @a.route("/request/<id>", ("GET", "POST"))
    def request(req, id):
        r, crit, bids = load(id)
        if req.method == "POST":
            if r["status"] != "open":
                raise Fail("This request is closed", 409)
            act = req.text("action")
            if act == "bid":
                cur = a.run("INSERT INTO bids (request, vendor, price, compliant) VALUES (?, ?, ?, ?)", id, req.text("vendor"), req.number("price", low=0.01), 0 if req.get("noncompliant") else 1)
                record("bid received", request=int(id), vendor=req.text("vendor"), price=req.number("price"))
            elif act == "mark":
                member = req.text("member")
                if a.row("SELECT 1 FROM conflicts WHERE request = ? AND member = ?", id, member):
                    raise Fail(f"{member} declared a conflict of interest on this request, and cannot mark it", 403)
                bid = a.one("SELECT id FROM bids WHERE request = ? AND vendor = ?", id, req.text("vendor"))
                if bid is None:
                    raise Fail("No such bid")
                a.run("INSERT OR REPLACE INTO marks (bid, criterion, member, mark) VALUES (?, ?, ?, ?)", bid, req.text("criterion"), member, req.number("mark", low=0, high=100))
                record("mark entered", request=int(id), vendor=req.text("vendor"), criterion=req.text("criterion"), member=member, mark=req.number("mark"))
            elif act == "conflict":
                a.run("INSERT OR IGNORE INTO conflicts (request, member) VALUES (?, ?)", id, req.text("member"))
                record("conflict declared", request=int(id), member=req.text("member"))
            elif act == "award":
                rows = core.evaluate(bids, crit)
                if not rows or not rows[0]["compliant"]:
                    raise Fail("There is no compliant bid to award")
                reason = req.text("reason", False)
                if rows[0]["vendor"] != req.text("vendor") and not reason:
                    raise Fail("Awarding to anyone but the top bid needs a written reason")
                a.run("UPDATE requests SET status = 'awarded', winner = ? WHERE id = ?", req.text("vendor"), id)
                record("award", request=int(id), vendor=req.text("vendor"), reason=reason)
            return redirect(f"/request/{id}")
        name, needed = core.method_for(r["estimate"])
        rows = core.evaluate(bids, crit) if bids else []
        conflicts = [x["member"] for x in a.rows("SELECT member FROM conflicts WHERE request = ?", id)]
        flags = core.red_flags(bids, needed, conflicts, r["estimate"])
        turns = core.sensitivity(bids, crit) if len(bids) > 1 else []
        body = stats([("Method", name), ("Quotes needed", needed), ("Bids", len(bids)), ("Status", r["status"])])
        if flags:
            body += card("Points to check", "".join(f'<p class="warn">{h(x)}</p>' for x in flags))
        cols = [("Rank", "rank"), ("Vendor", "vendor"), ("Price", lambda x: f"{x['price']:,.2f}")] + [(c.name, (lambda n: lambda x: f"{x['detail'][n]:.1f}")(c.name)) for c in crit] + [("Total", lambda x: "non-compliant" if x["total"] is None else f"{x['total']:.1f}")]
        body += card("Evaluation", table(rows, cols, "No bids yet."))
        if rows:
            body += card("Draft award note", f"<p>{h(core.award_text(r['title'], rows, crit))}</p>" + (f'<p class="warn">Close result: a {turns[0][0]} to {turns[0][1]} weight shift would favour {h(turns[0][2])}.</p>' if turns else ""))
        if r["status"] == "open":
            vendors = [(b.vendor, b.vendor) for b in bids]
            body += card("Add a bid", form(a, f"/request/{id}", [("action", "", "hidden", "bid"), ("vendor", "Vendor"), ("price", "Price", "number"), ("noncompliant", "Does not meet requirements", "checkbox", "1")], "Add"))
            body += card("Enter a mark", form(a, f"/request/{id}", [("action", "", "hidden", "mark"), ("vendor", "Vendor", "select", vendors), ("criterion", "Criterion", "select", [(c.name, c.name) for c in crit if c.kind != "price"]), ("member", "Panel member"), ("mark", "Mark out of 100", "number")], "Save"))
            body += card("Declare a conflict of interest", form(a, f"/request/{id}", [("action", "", "hidden", "conflict"), ("member", "Panel member")], "Declare"))
            body += card("Award", form(a, f"/request/{id}", [("action", "", "hidden", "award"), ("vendor", "Vendor", "select", vendors), ("reason", "Reason, if not the top bid", "textarea")], "Award"))
        return a.page(r["title"], body)

    @a.route("/audit")
    def audit(req):
        c = chain()
        broken = core.verify_chain(c)
        note = '<p class="good">The trail is intact.</p>' if broken is None else f'<p class="bad">The trail was altered at or before line {broken + 1}.</p>'
        return a.page("Audit trail", card("Every action, chained by hash", note + table(list(reversed(c)), [("When", lambda r: r["record"]["at"]), ("Event", lambda r: r["record"]["event"]), ("Details", lambda r: json.dumps({k: v for k, v in r["record"].items() if k not in ("at", "event")}, ensure_ascii=False)), ("Hash", lambda r: r["hash"][:12])], "Nothing recorded yet.")))

    return a
