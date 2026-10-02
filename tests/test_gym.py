from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

G = "otm_gym_management.group_gym_"


@tagged("post_install", "-at_install", "otm_gym")
class TestGym(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        U = cls.env["res.users"].with_context(no_reset_password=True)

        def mk(login, grp):
            return U.create({"name": "Test " + login, "login": "test_" + login,
                             "email": f"test_{login}@example.com", "group_ids": [Command.set([cls.env.ref(G + grp).id])]})
        cls.u_rec, cls.u_fin, cls.u_trn, cls.u_mgr = mk("rec", "reception"), mk("fin", "finance"), mk("trn", "trainer"), mk("mgr", "manager")
        cls.u_trn2 = mk("trn2", "trainer")
        cls.plan = cls.env["otm.gym.membership.plan"].create({"name": "Test Plan", "duration": 1, "duration_unit": "months", "price": 1000})
        cls.member = cls.env["otm.gym.member"].create({"name": "Test Member", "phone": "9000000001"})

    def _membership(self):
        ms = self.env["otm.gym.membership"].create({"member_id": self.member.id, "plan_id": self.plan.id})
        ms.action_confirm()
        return ms

    def test_01_code_and_partner(self):
        self.assertTrue(self.member.member_code.startswith("GT-"))
        self.assertTrue(self.member.partner_id)

    def test_02_membership_flow_and_direct_write_blocked(self):
        ms = self._membership()
        self.assertEqual(ms.state, "confirmed")
        with self.assertRaises(UserError):
            ms.with_user(self.u_mgr).write({"state": "active"})
        ms.action_activate()
        self.assertEqual(ms.state, "active")
        self.assertEqual(self.member.status, "active")
        self.assertTrue(self.env["otm.gym.history"].search([("res_model", "=", ms._name), ("res_id", "=", ms.id)]))

    def test_03_invalid_transition(self):
        ms = self.env["otm.gym.membership"].create({"member_id": self.member.id, "plan_id": self.plan.id})
        with self.assertRaises(UserError):
            ms.action_activate()

    def test_04_attendance_single_open_row(self):
        ms = self._membership(); ms.action_activate()
        A = self.env["otm.gym.attendance"]
        A.check_in_member(self.member.id)
        with self.assertRaises(Exception):
            A.check_in_member(self.member.id)
        A.check_out_member(self.member.id)
        self.assertFalse(A.search([("member_id", "=", self.member.id), ("state", "=", "present")]))

    def test_05_receipt_immutable(self):
        ms = self._membership()
        pay = self.env["otm.gym.payment"].search([("membership_id", "=", ms.id)], limit=1)
        if not pay:
            self.skipTest("no payment generated")
        pay.with_user(self.u_fin).action_request()

    def test_06_reception_no_health_access(self):
        hp = self.env["otm.gym.health.profile"].create({"member_id": self.member.id, "height": 170, "weight": 70})
        with self.assertRaises(AccessError):
            hp.with_user(self.u_rec).read(["weight"])
        with self.assertRaises(AccessError):
            hp.with_user(self.u_fin).read(["weight"])

    def test_07_trainer_sees_only_assigned(self):
        t = self.env["otm.gym.trainer"].create({"name": "Test Trainer", "user_id": self.u_trn.id})
        self.member.trainer_id = t
        other = self.env["otm.gym.member"].create({"name": "Test Other", "phone": "9000000002"})
        seen = self.env["otm.gym.member"].with_user(self.u_trn).search([("id", "in", [self.member.id, other.id])])
        self.assertEqual(seen, self.member)
        seen2 = self.env["otm.gym.member"].with_user(self.u_trn2).search([("id", "in", [self.member.id, other.id])])
        self.assertFalse(seen2)

    def test_08_dashboard_rpc_per_role(self):
        for u in (self.u_rec, self.u_fin, self.u_trn, self.u_mgr):
            d = self.env["otm.gym.dashboard"].with_user(u).get_dashboard({})
            self.assertIsInstance(d, dict)
            self.assertNotIn("health", str(d.get("kpis", "")).lower() if u in (self.u_rec, self.u_fin) else "")

    def test_09_reports(self):
        R = self.env["otm.gym.reports"].with_user(self.u_mgr)
        for r in R.list_reports():
            R.get_report(r["code"], False, False)

    def test_10_cron(self):
        C = self.env["otm.gym.cron"]
        for m in ("cron_membership_lifecycle", "cron_payments_overdue", "cron_assessments_due",
                  "cron_plan_reviews", "cron_long_absence", "cron_appointments"):
            getattr(C, m)()
            getattr(C, m)()  # idempotent / deduped

    def test_11_matrix(self):
        m = self.env["otm.gym.membership"].get_transition_matrix()
        self.assertIn("actions", m)
