"""Referral attribution and ledger invariants; no live user store is opened."""
import tempfile
import time
import unittest
from pathlib import Path

from referrals import Referrals


class ReferralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Referrals(Path(self.tmp.name) / "referrals.db")
        self.code = self.store.link(10)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_stable_link_window_duplicate_and_self_referral(self):
        self.assertEqual(self.code, self.store.link(10))
        old = self.store.visit(self.code, now=100)
        self.assertFalse(self.store.signup(11, old, days=30, touch="last", now=100+31*86400))
        self_token = self.store.visit(self.code, now=200)
        self.assertFalse(self.store.signup(10, self_token, days=30, touch="last", now=201))
        token = self.store.visit(self.code, now=300)
        self.assertTrue(self.store.signup(11, token, days=30, touch="last", now=301))
        self.assertFalse(self.store.signup(11, token, days=30, touch="last", now=302))
        self.assertFalse(self.store.signup(12, token, days=30, touch="last", now=303))
        self.assertEqual(self.store.summary(10)["signups"], 1)
        self.assertEqual(self.store.summary(12)["signups"], 0)

    def test_payment_idempotency_partial_refund_and_totals(self):
        token = self.store.visit(self.code)
        self.assertTrue(self.store.signup(11, token, days=30, touch="last"))
        self.assertEqual(self.store.payment("e1", "pay1", 11, 49900, 2500, 365), "pending")
        self.assertEqual(self.store.payment("e1", "pay1", 11, 49900, 2500, 365), "duplicate")
        self.assertEqual(self.store.payment("e2", "pay1", 11, 49900, 2500, 365), "payment already recorded")
        self.assertEqual(self.store.summary(10)["totals_paise"]["pending"], 12475)
        self.assertEqual(self.store.reverse("e3", "rf1", "pay1", 10000), "pending")
        self.assertEqual(self.store.reverse("e3", "rf1", "pay1", 10000), "duplicate")
        self.assertEqual(self.store.summary(10)["totals_paise"]["pending"], 9975)
        self.assertEqual(self.store.reverse("e4", "rf2", "pay1", 39900), "reversed")
        self.assertEqual(self.store.summary(10)["totals_paise"]["pending"], 0)

    def test_admin_approval_payout_and_paid_refund_exception(self):
        token = self.store.visit(self.code)
        self.store.signup(11, token, days=30, touch="last")
        self.store.payment("e1", "pay1", 11, 50000, 2500, 365)
        self.assertFalse(self.store.approve("missing", "admin"))
        self.assertTrue(self.store.approve("pay1", "admin"))
        with self.assertRaises(ValueError):
            self.store.payout(12, ["pay1"], "transfer1", "admin", 1000)
        self.assertEqual(self.store.payout(10, ["pay1"], "transfer1", "admin", 1000), 12500)
        self.assertEqual(self.store.summary(10)["totals_paise"]["paid"], 12500)
        self.assertEqual(self.store.reverse("e2", "rf1", "pay1", 50000), "exception")
        self.assertEqual(len(self.store.exceptions()), 1)
        self.assertEqual(self.store.summary(10)["totals_paise"]["exception"], 0)
        with self.assertRaises(Exception):
            self.store.payout(10, ["pay1"], "transfer1", "admin", 1000)

    def test_dispute_holds_and_chargeback_reverses(self):
        token = self.store.visit(self.code)
        self.store.signup(11, token, days=30, touch="last")
        self.store.payment("e1", "pay1", 11, 50000, 2500, 365)
        self.assertEqual(self.store.dispute("e2", "disp1", "pay1", 50000, "created"), "held for dispute review")
        self.assertFalse(self.store.approve("pay1", "admin"))
        self.assertEqual(self.store.dispute("e3", "disp1", "pay1", 50000, "won"), "dispute won")
        self.assertTrue(self.store.approve("pay1", "admin"))
        self.assertEqual(self.store.dispute("e4", "disp2", "pay1", 50000, "lost"), "reversed")
        self.assertEqual(self.store.summary(10)["totals_paise"]["approved"], 0)

    def test_refund_arriving_before_capture_is_replayed(self):
        token = self.store.visit(self.code)
        self.store.signup(11, token, days=30, touch="last")
        self.assertEqual(self.store.reverse("refund-first", "rf1", "pay1", 50000), "unmatched reversal")
        self.store.payment("capture-later", "pay1", 11, 50000, 2500, 365)
        self.assertEqual(self.store.summary(10)["totals_paise"]["pending"], 0)

    def test_same_provider_customer_across_accounts_needs_review(self):
        self.store.signup(11, self.store.visit(self.code), days=30, touch="last")
        self.store.signup(12, self.store.visit(self.code), days=30, touch="last")
        self.store.payment("e1", "pay1", 11, 50000, 2500, 365, customer_key="hash")
        self.assertEqual(self.store.payment("e2", "pay2", 12, 50000, 2500, 365, customer_key="hash"), "duplicate provider customer: review")
        self.assertFalse(self.store.approve("pay2", "admin"))
        self.assertTrue(self.store.resolve("pay2", "admin", "clear", "verified separate person"))
        self.assertTrue(self.store.approve("pay2", "admin"))

    def test_terms_at_signup_freeze_rate_and_duration(self):
        self.store.signup(11, self.store.visit(self.code), days=30, touch="last",
                          rate_bps=2000, duration_days=365, terms_version="v1")
        self.store.payment("e1", "pay1", 11, 50000, 3000, 30)
        row = self.store.db.execute("SELECT rate_bps,earned_paise FROM commissions WHERE payment_id='pay1'").fetchone()
        self.assertEqual(tuple(row), (2000, 10000))
        self.assertEqual(self.store.db.execute("SELECT terms_version FROM signups WHERE user_id=11").fetchone()[0], "v1")

    def test_payment_uses_provider_paid_date_for_duration(self):
        now = int(time.time())
        self.store.signup(11, self.store.visit(self.code, now=now), days=30,
                          touch="last", now=now, rate_bps=2000, duration_days=30)
        self.assertEqual(self.store.payment("late-delivery", "pay1", 11, 50000, 2000, 30,
                                            paid_at=now + 31*86400), "no eligible signup")
        self.assertEqual(self.store.summary(10)["paid_customers"], 0)


if __name__ == "__main__":
    unittest.main()


class ConfiguredTests(unittest.TestCase):
    """Production 2026-10-16: the validation block had drifted below
    public_terms()'s return, so configured() returned None with every setting
    present and the programme could never open."""

    FULL = {
        "PIVOT_REFERRALS_ENABLED": "1", "PIVOT_REFERRAL_RATE_BPS": "1500",
        "PIVOT_REFERRAL_ATTRIBUTION_DAYS": "30", "PIVOT_REFERRAL_TOUCH": "last",
        "PIVOT_REFERRAL_PAYOUT_MIN_PAISE": "100000", "PIVOT_REFERRAL_COMMISSION_DAYS": "365",
        "PIVOT_REFERRAL_TERMS_VERSION": "v1", "RAZORPAY_KEY_ID": "k", "RAZORPAY_KEY_SECRET": "s",
        "RAZORPAY_WEBHOOK_SECRET": "w", "PIVOT_REFERRAL_IDENTITY_KEY": "i",
        "PIVOT_REFERRAL_ELIGIBILITY": "e", "PIVOT_REFERRAL_REVENUE_BASIS": "r",
        "PIVOT_REFERRAL_REFUND_POLICY": "p", "PIVOT_REFERRAL_PAYOUT_SCHEDULE": "m",
        "PIVOT_REFERRAL_PAYOUT_METHOD": "b", "PIVOT_REFERRAL_TERMS_CHANGE_POLICY": "c",
        "PIVOT_REFERRAL_EFFECTIVE_DATE": "2026-10-16",
    }

    def _with(self, **over):
        from unittest import mock
        import referrals
        env = {**self.FULL, **over}
        env = {k: v for k, v in env.items() if v is not None}
        with mock.patch.dict("os.environ", env, clear=True):
            return referrals.configured(), (referrals.public_terms() if referrals.configured() else None)

    def test_full_settings_open_the_programme(self):
        ok, terms = self._with()
        self.assertIs(ok, True)
        self.assertEqual(terms["rate_bps"], 1500)
        self.assertEqual(terms["payout_min_paise"], 100000)

    def test_missing_or_invalid_settings_keep_it_closed(self):
        self.assertIs(self._with(PIVOT_REFERRALS_ENABLED="0")[0], False)
        self.assertIs(self._with(PIVOT_REFERRAL_PAYOUT_METHOD=None)[0], False)
        self.assertIs(self._with(PIVOT_REFERRAL_TOUCH="first")[0], False)
        self.assertIs(self._with(PIVOT_REFERRAL_RATE_BPS="0")[0], False)
        self.assertIs(self._with(PIVOT_REFERRAL_ATTRIBUTION_DAYS="thirty")[0], False)
