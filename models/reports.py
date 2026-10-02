"""Report API (section 57). Every report runs with the caller's access rights, so a role only gets rows it may see."""
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .dashboard import PRESENT

REPORTS = [
    ("members", "Members"), ("attendance", "Attendance"), ("membership", "Memberships"), ("renewals", "Renewals"),
    ("revenue", "Revenue"), ("payments", "Payments"), ("trainer_workload", "Trainer workload"),
    ("weight_progress", "Weight progress"), ("body_fat_progress", "Body fat progress"),
    ("workout_adherence", "Workout adherence"), ("diet_status", "Diet status"),
]


class GymReports(models.AbstractModel):
    _name = "otm.gym.reports"
    _description = "Gym Reports API"

    @api.model
    def list_reports(self):
        """Only reports the user can actually run (model read access decides)."""
        need = {"members": "otm.gym.member", "attendance": "otm.gym.attendance", "membership": "otm.gym.membership",
                "renewals": "otm.gym.membership", "revenue": "otm.gym.payment.receipt", "payments": "otm.gym.payment",
                "trainer_workload": "otm.gym.appointment", "weight_progress": "otm.gym.assessment",
                "body_fat_progress": "otm.gym.assessment", "workout_adherence": "otm.gym.workout.plan",
                "diet_status": "otm.gym.diet.plan"}
        return [{"code": c, "title": t} for c, t in REPORTS if self.env[need[c]].has_access("read")]

    @api.model
    def get_report(self, code, date_from=None, date_to=None):
        today = fields.Date.context_today(self)
        d1 = fields.Date.to_date(date_to) if date_to else today
        d0 = fields.Date.to_date(date_from) if date_from else d1.replace(day=1)
        if d1 < d0 or (d1 - d0).days > 731:
            raise UserError(_("Choose a valid period of at most two years."))
        fn = getattr(self, "_rep_%s" % code, None)
        if not fn:
            raise UserError(_("Unknown report."))
        title = dict(REPORTS)[code]
        cols, rows = fn(d0, d1, today)
        return {"code": code, "title": title, "date_from": str(d0), "date_to": str(d1), "columns": cols,
                "rows": [[("" if v is False or v is None else str(v) if hasattr(v, "isoformat") else v) for v in r] for r in rows]}

    @staticmethod
    def _c(key, label, typ="text"):
        return {"key": key, "label": label, "type": typ}

    def _rep_members(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.member"].search_read([("join_date", ">=", d0), ("join_date", "<=", d1)],
                                                      ["member_code", "name", "phone", "status", "plan_id", "membership_expiry", "trainer_id", "join_date"],
                                                      order="join_date desc")
        return ([c("code", "Member ID"), c("name", "Name"), c("phone", "Phone"), c("status", "Status"), c("plan", "Plan"),
                 c("expiry", "Expiry", "date"), c("trainer", "Trainer"), c("join", "Joined", "date")],
                [[r["member_code"], r["name"], r["phone"], r["status"], r["plan_id"] and r["plan_id"][1], r["membership_expiry"],
                  r["trainer_id"] and r["trainer_id"][1], r["join_date"]] for r in rows])

    def _rep_attendance(self, d0, d1, today):
        c = self._c
        t = self.env["otm.gym.dashboard"].get_attendance_trend("custom", d0, d1)
        s = {x["key"]: x["values"] for x in t["series"]}
        return ([c("date", "Date"), c("present", "Present", "number"), c("expected", "Expected", "number"),
                 c("absent", "Absent", "number"), c("pct", "Attendance %", "pct")],
                [[t["labels"][i], s["present"][i], s["expected"][i], s["absent"][i], s["percent"][i]] for i in range(len(t["labels"]))])

    def _rep_membership(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.membership"].search_read([("start_date", ">=", d0), ("start_date", "<=", d1)],
                                                          ["name", "member_id", "plan_id", "start_date", "end_date", "final_amount", "payment_status", "state"],
                                                          order="start_date desc")
        return ([c("ref", "Reference"), c("member", "Member"), c("plan", "Plan"), c("start", "Start", "date"), c("end", "End", "date"),
                 c("amount", "Amount", "money"), c("pay", "Payment"), c("state", "State")],
                [[r["name"], r["member_id"][1], r["plan_id"][1], r["start_date"], r["end_date"], r["final_amount"], r["payment_status"], r["state"]] for r in rows])

    def _rep_renewals(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.membership"].search_read(
            [("end_date", ">=", d0), ("end_date", "<=", d1), ("state", "in", ("active", "expired", "renewal_pending", "renewed"))],
            ["member_id", "plan_id", "end_date", "state", "renewal_stage"], order="end_date asc")
        return ([c("member", "Member"), c("plan", "Plan"), c("end", "Ends", "date"), c("days", "Days left", "number"),
                 c("state", "State"), c("stage", "Renewal stage")],
                [[r["member_id"][1], r["plan_id"][1], r["end_date"], (r["end_date"] - today).days, r["state"], r["renewal_stage"]] for r in rows])

    def _rep_revenue(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.payment.receipt"]._read_group([("date", ">=", d0), ("date", "<=", d1)], ["date:day", "method"], ["amount:sum"])
        return ([c("date", "Date", "date"), c("method", "Method"), c("amount", "Net received", "money")],
                sorted([[d, m or "", a] for d, m, a in rows], key=lambda r: r[0], reverse=True))

    def _rep_payments(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.payment"].search_read([("create_date", ">=", d0), ("create_date", "<", d1 + timedelta(days=1))],
                                                       ["name", "member_id", "kind", "amount", "paid_amount", "balance", "due_date", "state"], order="id desc")
        return ([c("ref", "Reference"), c("member", "Member"), c("kind", "Kind"), c("amount", "Amount", "money"), c("paid", "Paid", "money"),
                 c("balance", "Outstanding", "money"), c("due", "Due", "date"), c("state", "State")],
                [[r["name"], r["member_id"][1], r["kind"], r["amount"], r["paid_amount"], r["balance"], r["due_date"], r["state"]] for r in rows])

    def _rep_trainer_workload(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.dashboard"].get_trainer_summary()
        return ([c("t", "Trainer"), c("m", "Assigned members", "number"), c("a", "Today's sessions", "number"),
                 c("d", "Completed", "number"), c("p", "Pending", "number")],
                [[r["trainer"], r["members"], r["today"], r["completed"], r["pending"]] for r in rows])

    def _progress_report(self, d0, d1, field):
        A, P = self.env["otm.gym.assessment"], self.env["otm.gym.progress"]
        pts = {}
        for r in A.search_read([("state", "=", "done"), ("date", "<=", d1)], ["member_id", "date", field], order="date asc, id asc"):
            if r[field]:
                pts.setdefault(r["member_id"], []).append((r["date"], r[field]))
        for r in P.search_read([("date", "<=", d1)], ["member_id", "date", field], order="date asc, id asc"):
            if r[field]:
                pts.setdefault(r["member_id"], []).append((r["date"], r[field]))
        out = []
        for m, v in pts.items():
            v.sort()
            first = next((x for x in v if x[0] >= d0), v[0])
            out.append([m[1], first[1], v[-1][1], round(v[-1][1] - first[1], 1), len(v)])
        return sorted(out, key=lambda r: r[3])

    def _rep_weight_progress(self, d0, d1, today):
        c = self._c
        return ([c("m", "Member"), c("a", "Start (kg)", "number"), c("b", "Latest (kg)", "number"),
                 c("d", "Change", "number"), c("n", "Readings", "number")], self._progress_report(d0, d1, "weight"))

    def _rep_body_fat_progress(self, d0, d1, today):
        c = self._c
        return ([c("m", "Member"), c("a", "Start %", "number"), c("b", "Latest %", "number"),
                 c("d", "Change", "number"), c("n", "Readings", "number")], self._progress_report(d0, d1, "body_fat"))

    def _rep_workout_adherence(self, d0, d1, today):
        c = self._c
        plans = self.env["otm.gym.workout.plan"].search([("state", "=", "active")])
        since = today - timedelta(days=28)
        visits = {m.id: n for m, n in self.env["otm.gym.attendance"]._read_group(
            [("member_id", "in", plans.member_id.ids), ("date", ">=", since), ("state", "in", PRESENT)],
            ["member_id"], ["date:count_distinct"])}
        rows = []
        for p in plans:
            per_week = len(set(p.line_ids.mapped("day")))
            planned = per_week * 4
            v = visits.get(p.member_id.id, 0)
            rows.append([p.member_id.name, p.name, per_week, planned, v, round(min(v * 100.0 / planned, 100), 1) if planned else 0])
        return ([c("m", "Member"), c("p", "Plan"), c("w", "Days / week", "number"), c("pl", "Planned (4 wk)", "number"),
                 c("v", "Visits (4 wk)", "number"), c("a", "Adherence %", "pct")], sorted(rows, key=lambda r: r[5]))

    def _rep_diet_status(self, d0, d1, today):
        c = self._c
        rows = self.env["otm.gym.diet.plan"].search_read([("create_date", "<", d1 + timedelta(days=1))],
                                                         ["name", "member_id", "version", "calories_target", "calories_total", "end_date", "state"], order="id desc")
        return ([c("p", "Plan"), c("m", "Member"), c("v", "Version", "number"), c("t", "Target kcal", "number"),
                 c("c", "Planned kcal", "number"), c("d", "Variance", "number"), c("e", "Ends", "date"), c("s", "State")],
                [[r["name"], r["member_id"][1], r["version"], r["calories_target"], round(r["calories_total"]),
                  round(r["calories_total"] - r["calories_target"]), r["end_date"], r["state"]] for r in rows])
