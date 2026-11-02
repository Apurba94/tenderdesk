import json
import unittest

from tenderdesk import core, create_app
from tenderdesk.core import Bid, Criterion, append, award_text, check_weights, evaluate, method_for, red_flags, sensitivity, verify_chain

C = [Criterion("Price", 50, "price"), Criterion("Quality", 50)]


def bid(v, p, q=None, ok=True):
    return Bid(v, p, {"Quality": {"A": q}} if q is not None else {}, ok)


class TestRules(unittest.TestCase):
    def test_methods(self):
        self.assertEqual(method_for(100), ("direct purchase", 1))
        self.assertEqual(method_for(100000)[1], 3)
        self.assertEqual(method_for(10 ** 7)[0], "open tender")

    def test_weights(self):
        with self.assertRaises(ValueError):
            check_weights([Criterion("Price", 50, "price")])
        with self.assertRaises(ValueError):
            check_weights([Criterion("Q", 100)])


class TestEvaluate(unittest.TestCase):
    def test_price_points(self):
        rows = evaluate([bid("A", 100, 80), bid("B", 200, 80)], C)
        self.assertEqual(rows[0]["vendor"], "A")
        self.assertEqual(rows[0]["detail"]["Price"], 100)
        self.assertEqual(rows[1]["detail"]["Price"], 50)
        self.assertEqual(rows[0]["total"], 90)

    def test_quality_can_beat_price(self):
        self.assertEqual(evaluate([bid("Cheap", 100, 10), bid("Good", 130, 100)], C)[0]["vendor"], "Good")

    def test_panel_marks_are_averaged(self):
        b = Bid("A", 100, {"Quality": {"x": 60, "y": 80}})
        self.assertEqual(evaluate([b], C)[0]["detail"]["Quality"], 70)

    def test_non_compliant_bids_cannot_win_or_set_the_price(self):
        rows = evaluate([bid("Bad", 1, 100, False), bid("Good", 100, 50)], C)
        self.assertEqual(rows[0]["vendor"], "Good")
        self.assertEqual(rows[0]["detail"]["Price"], 100)
        self.assertIsNone(rows[1]["rank"])

    def test_price_only_tender_goes_to_the_lowest(self):
        rows = evaluate([bid("A", 100, 90), bid("B", 200, 40)], [Criterion("Price", 100, "price")])
        self.assertEqual(rows[0]["vendor"], "A")

    def test_sensitivity_spots_a_close_result(self):
        self.assertTrue(sensitivity([bid("A", 100, 50), bid("B", 110, 58)], C, 10))
        self.assertEqual(sensitivity([bid("A", 100, 100), bid("B", 300, 10)], C, 10), [])


class TestFlags(unittest.TestCase):
    def test_too_few_quotes(self):
        self.assertIn("only 1 quotes", red_flags([bid("A", 100)], 3)[0])

    def test_suspiciously_close_prices(self):
        self.assertTrue(any("within 1%" in x for x in red_flags([bid("A", 1000), bid("B", 1001), bid("C", 1002)], 3)))

    def test_very_low_price(self):
        self.assertTrue(any("realistic" in x for x in red_flags([bid("A", 100), bid("B", 200)], 2)))

    def test_over_estimate(self):
        self.assertTrue(any("above the estimate" in x for x in red_flags([bid("A", 200)], 1, estimate=100)))

    def test_conflict_and_panel_disagreement(self):
        b = Bid("A", 100, {"Quality": {"x": 10, "y": 90, "z": 50}})
        flags = red_flags([b], 1, ["Karim"])
        self.assertTrue(any("Karim" in x for x in flags))
        self.assertTrue(any("differ by 80" in x for x in flags))

    def test_a_normal_set_has_no_flags(self):
        self.assertEqual(red_flags([bid("A", 100), bid("B", 120), bid("C", 130)], 3, estimate=120), [])

    def test_award_note(self):
        text = award_text("Chairs", evaluate([bid("A", 100, 90), bid("B", 150, 60)], C), C)
        self.assertIn("A scored highest", text)
        self.assertIn("B (", text)
        self.assertIn("No compliant", award_text("x", [], C))


class TestChain(unittest.TestCase):
    def build(self):
        c = []
        for i in range(5):
            append(c, {"n": i})
        return c

    def test_good(self):
        self.assertIsNone(verify_chain(self.build()))
        self.assertIsNone(verify_chain([]))

    def test_changing_a_record_is_found_where_it_happened(self):
        c = self.build()
        c[2]["record"]["n"] = 99
        self.assertEqual(verify_chain(c), 2)

    def test_removing_a_line_is_found(self):
        c = self.build()
        del c[2]
        self.assertEqual(verify_chain(c), 2)

    def test_reordering_is_found(self):
        c = self.build()
        c[1], c[2] = c[2], c[1]
        self.assertEqual(verify_chain(c), 1)

    def test_forging_a_line_breaks_all_that_follow(self):
        c = self.build()
        c[1] = {"record": {"n": 7}, "hash": core.link(c[0]["hash"], {"n": 7})}
        self.assertEqual(verify_chain(c), 2)


class TestApp(unittest.TestCase):
    def setUp(self):
        self.a = create_app(":memory:")
        self.url = self.a.post("/new", title="Laptops", estimate=100000, criteria="Price, 50, price\nQuality, 50").headers["Location"]
        self.a.post(self.url, action="bid", vendor="A", price=90000)
        self.a.post(self.url, action="bid", vendor="B", price=100000)
        self.a.post(self.url, action="mark", vendor="A", criterion="Quality", member="M1", mark=60)
        self.a.post(self.url, action="mark", vendor="B", criterion="Quality", member="M1", mark=90)

    def text(self, url=None):
        return self.a.get(url or self.url).body.decode()

    def test_page(self):
        page = self.text()
        self.assertIn("request for quotations", page)
        self.assertIn("only 2 quotes", page)
        self.assertIn("B scored highest", page)

    def test_conflict_blocks_marking(self):
        self.a.post(self.url, action="conflict", member="M2")
        r = self.a.post(self.url, action="mark", vendor="A", criterion="Quality", member="M2", mark=100)
        self.assertEqual(r.status, 403)

    def test_awarding_the_top_bid(self):
        self.assertEqual(self.a.post(self.url, action="award", vendor="B").status, 303)
        self.assertEqual(self.a.one("SELECT winner FROM requests"), "B")
        self.assertEqual(self.a.post(self.url, action="bid", vendor="C", price=1).status, 409)

    def test_passing_over_the_top_bid_needs_a_reason(self):
        self.assertEqual(self.a.post(self.url, action="award", vendor="A").status, 400)
        self.assertEqual(self.a.post(self.url, action="award", vendor="A", reason="B could not deliver in time").status, 303)
        self.assertIn("could not deliver", self.text("/audit"))

    def test_audit_trail_detects_tampering(self):
        self.assertIn("The trail is intact", self.text("/audit"))
        rec = json.loads(self.a.one("SELECT record FROM chain WHERE id = 2"))
        rec["price"] = 1
        self.a.run("UPDATE chain SET record = ? WHERE id = 2", json.dumps(rec))
        self.assertIn("altered at or before line 2", self.text("/audit"))

    def test_bad_inputs(self):
        self.assertEqual(self.a.post("/new", title="x", estimate=1, criteria="Price, 40, price\nQ, 40").status, 400)
        self.assertEqual(self.a.post(self.url, action="bid", vendor="A", price=1).status, 409)
        self.assertEqual(self.a.get("/request/9").status, 404)


if __name__ == "__main__":
    unittest.main()
