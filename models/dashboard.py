"""Dashboard / Customer-360 API consumed by the Next.js Gym app (one RPC per screen, never dozens).

Everything is computed from Odoo records with the CURRENT USER's access rights and record rules.
Health-related data is never added to dashboard payloads; per-member health sections are fetched
through models the user is allowed to read, otherwise they come back as {"restricted": true}.
"""
from datetime import date, datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

ROLE_ORDER = ["admin", "manager", "reception", "finance", "trainer", "nutritionist", "assessor", "customer"]
DAY_KEYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PRESENT = ("present", "checked_out")
LIVE = ("active", "renewal_pending")
TYPE_LABEL = {"personal_training": "Personal Training", "assessment": "Assessment",
              "diet_consultation": "Diet Consultation", "fitness_consultation": "Fitness Consultation", "other": "Other"}


class GymDashboard(models.AbstractModel):
    _name = "otm.gym.dashboard"
    _description = "Gym Dashboard API"

    # ------------------------------------------------------------------ utils
    @api.model
    def _role(self):
        for r in ROLE_ORDER:
            if self.env.user.has_group("otm_gym_management.group_gym_%s" % r):
                return r
        return False

    @api.model
    def _is(self, *roles):
        return any(self.env.user.has_group("otm_gym_management.group_gym_%s" % r) for r in roles)

    @api.model
    def _today(self):
        return fields.Date.context_today(self)

    @api.model
    def _local(self, dt):
        return fields.Datetime.context_timestamp(self, dt) if dt else False

    @api.model
    def _hhmm(self, dt):
        return self._local(dt).strftime("%H:%M") if dt else ""

    @api.model
    def _hhmm12(self, dt):
        return self._local(dt).strftime("%I:%M %p").lstrip("0") if dt else ""

    @api.model
    def _greeting(self):
        h = self._local(fields.Datetime.now()).hour
        return "Good morning" if h < 12 else "Good afternoon" if h < 17 else "Good evening"

    @api.model
    def _photo_map(self, model, ids):
        """{id: cache-bust} for records that really have a photo (one query, no binary loading)."""
        if not ids:
            return {}
        att = self.env["ir.attachment"].sudo().search_read(
            [("res_model", "=", model), ("res_field", "=", "image_1920"), ("res_id", "in", list(ids))],
            ["res_id", "write_date"])
        return {a["res_id"]: str(a["write_date"])[:19] for a in att}

    @api.model
    def _kpi(self, key, label, value, tone="idle", model=None, domain=None, name=None, kind="count"):
        return {"key": key, "label": label, "value": value, "kind": kind, "tone": tone,
                "action": {"res_model": model, "domain": domain or [], "name": name or label} if model else None}

    # -------------------------------------------------------- building blocks
    @api.model
    def get_today_attendance(self):
        today = self._today()
        Att = self.env["otm.gym.attendance"]
        present = Att._read_group([("date", "=", today), ("state", "in", PRESENT)], [], ["member_id:count_distinct"])[0][0]
        inside = Att.search_count([("state", "=", "present")])
        expected = self._expected_by_day(today, today)[0]
        return {"date": str(today), "present": present, "inside": inside, "expected": expected,
                "absent": max(expected - present, 0), "percent": round(present * 100.0 / expected, 1) if expected else 0.0}

    @api.model
    def _expected_by_day(self, d0, d1):
        n = (d1 - d0).days + 1
        diff = [0] * (n + 1)
        rows = self.env["otm.gym.membership"].search_read(
            [("state", "in", ("active", "expired", "renewal_pending", "renewed")),
             ("start_date", "<=", d1), ("end_date", ">=", d0)], ["start_date", "end_date"])
        for r in rows:
            a = max((r["start_date"] - d0).days, 0)
            b = min((r["end_date"] - d0).days, n - 1)
            if a <= b:
                diff[a] += 1
                diff[b + 1] -= 1
        out, run = [], 0
        for i in range(n):
            run += diff[i]
            out.append(run)
        return out

    @api.model
    def get_attendance_trend(self, range_key="7d", date_from=None, date_to=None):
        today = self._today()
        if range_key == "today":
            rows = self.env["otm.gym.attendance"].search_read(
                [("date", "=", today), ("state", "in", PRESENT)], ["check_in"])
            buckets = [0] * 24
            for r in rows:
                if r["check_in"]:
                    buckets[self._local(r["check_in"]).hour] += 1
            lo = next((i for i, v in enumerate(buckets) if v), 5)
            lo, hi = min(lo, 5), max(max((i for i, v in enumerate(buckets) if v), default=21), 21)
            return {"range": "today", "labels": ["%02d:00" % h for h in range(lo, hi + 1)],
                    "series": [{"key": "present", "label": "Check-ins", "values": buckets[lo:hi + 1]}],
                    "summary": self.get_today_attendance()}
        days = {"7d": 7, "30d": 30, "90d": 90}.get(range_key, 7)
        d1, d0 = today, today - timedelta(days=days - 1)
        if range_key == "custom" and date_from and date_to:
            d0, d1 = fields.Date.to_date(date_from), fields.Date.to_date(date_to)
            if (d1 - d0).days > 366 or d1 < d0:
                raise UserError(_("Choose a range of at most one year."))
        n = (d1 - d0).days + 1
        got = dict(self.env["otm.gym.attendance"]._read_group(
            [("date", ">=", d0), ("date", "<=", d1), ("state", "in", PRESENT)], ["date:day"], ["member_id:count_distinct"]))
        expected = self._expected_by_day(d0, d1)
        labels, present, absent, pct = [], [], [], []
        for i in range(n):
            d = d0 + timedelta(days=i)
            p = got.get(d, 0)
            labels.append(d.strftime("%d %b"))
            present.append(p)
            absent.append(max(expected[i] - p, 0))
            pct.append(round(p * 100.0 / expected[i], 1) if expected[i] else 0.0)
        return {"range": range_key, "labels": labels, "date_from": str(d0), "date_to": str(d1), "series": [
            {"key": "present", "label": "Present", "values": present}, {"key": "absent", "label": "Absent", "values": absent},
            {"key": "expected", "label": "Expected", "values": expected}, {"key": "percent", "label": "Attendance %", "values": pct}]}

    @api.model
    def get_members_present(self, limit=60):
        today = self._today()
        rows = self.env["otm.gym.attendance"].search_read(
            [("date", "=", today), ("state", "in", PRESENT)],
            ["member_id", "check_in", "check_out", "trainer_id", "state"], order="check_in asc", limit=limit)
        return self._attendance_rows(rows)

    @api.model
    def _attendance_rows(self, rows):
        ids = [r["member_id"][0] for r in rows]
        members = {m["id"]: m for m in self.env["otm.gym.member"].search_read(
            [("id", "in", ids)], ["name", "member_code", "membership_state", "membership_expiry"])}
        photos = self._photo_map("otm.gym.member", ids)
        now = fields.Datetime.now()
        out = []
        for r in rows:
            m = members.get(r["member_id"][0], {})
            end = r["check_out"] or now
            out.append({
                "id": r["id"], "member_id": r["member_id"][0], "name": r["member_id"][1], "code": m.get("member_code"),
                "photo": photos.get(r["member_id"][0]), "check_in": self._hhmm12(r["check_in"]),
                "check_in_raw": str(r["check_in"]) if r["check_in"] else "", "check_out": self._hhmm12(r["check_out"]) if r["check_out"] else "",
                "minutes": int((end - r["check_in"]).total_seconds() // 60) if r["check_in"] else 0,
                "trainer": r["trainer_id"] and r["trainer_id"][1] or "", "state": r["state"],
                "membership_state": m.get("membership_state") or "", "expiry": str(m.get("membership_expiry") or "")})
        return out

    @api.model
    def get_currently_inside(self, limit=80):
        Att = self.env["otm.gym.attendance"]
        rows = Att.search_read([("state", "=", "present")], ["member_id", "check_in", "check_out", "trainer_id", "state"],
                               order="check_in asc", limit=limit)
        return {"count": Att.search_count([("state", "=", "present")]), "rows": self._attendance_rows(rows)}

    @api.model
    def get_today_sessions(self, limit=40):
        today = self._today()
        rows = self.env["otm.gym.appointment"].search_read(
            [("date", "=", today), ("state", "!=", "cancelled")],
            ["member_id", "trainer_id", "appointment_type", "start_datetime", "state", "name"], order="start_datetime asc", limit=limit)
        return [{"id": r["id"], "time": self._hhmm(r["start_datetime"]), "time12": self._hhmm12(r["start_datetime"]),
                 "member_id": r["member_id"][0], "member": r["member_id"][1], "trainer": r["trainer_id"][1],
                 "type": TYPE_LABEL.get(r["appointment_type"], r["appointment_type"]), "state": r["state"]} for r in rows]

    @api.model
    def get_membership_expiry(self, window=30, limit=50):
        today = self._today()
        M = self.env["otm.gym.membership"]
        base = [("state", "in", LIVE), ("end_date", ">=", today)]
        counts = {str(d): M.search_count(base + [("end_date", "<=", today + timedelta(days=d))]) for d in (7, 15, 30)}
        window = window if window in (7, 15, 30) else 30
        rows = M.search_read(base + [("end_date", "<=", today + timedelta(days=window))],
                             ["member_id", "plan_id", "end_date", "state", "renewal_stage"], order="end_date asc", limit=limit)
        photos = self._photo_map("otm.gym.member", [r["member_id"][0] for r in rows])
        return {"counts": counts, "window": window, "rows": [{
            "id": r["id"], "member_id": r["member_id"][0], "name": r["member_id"][1], "photo": photos.get(r["member_id"][0]),
            "plan": r["plan_id"][1], "end_date": str(r["end_date"]), "days_left": (r["end_date"] - today).days,
            "state": r["state"], "stage": r["renewal_stage"]} for r in rows]}

    @api.model
    def get_revenue_summary(self):
        if not self._is("finance", "manager"):
            return {"restricted": True}
        today = self._today()
        starts = {"today": today, "week": today - timedelta(days=today.weekday()),
                  "month": today.replace(day=1), "year": today.replace(month=1, day=1)}
        R = self.env["otm.gym.payment.receipt"]
        out = {}
        for k, d in starts.items():
            out[k] = R._read_group([("date", ">=", d), ("date", "<=", today)], [], ["amount:sum"])[0][0] or 0.0
        months = []
        first = today.replace(day=1)
        for i in range(5, -1, -1):
            y, m = first.year, first.month - i
            while m <= 0:
                m += 12
                y -= 1
            months.append(date(y, m, 1))
        got = dict(R._read_group([("date", ">=", months[0])], ["date:month"], ["amount:sum"]))
        return {"totals": out, "rows": [{"key": k, "label": k.title(), "value": out[k]} for k in ("today", "week", "month", "year")],
                "monthly": [{"key": str(m), "label": m.strftime("%b %y"), "value": got.get(m, 0.0)} for m in months]}

    @api.model
    def get_member_growth(self, granularity="daily"):
        today = self._today()
        g, span = {"weekly": ("week", 12), "monthly": ("month", 12)}.get(granularity, ("day", 30))
        if g == "day":
            starts = [today - timedelta(days=span - 1 - i) for i in range(span)]
        elif g == "week":
            mon = today - timedelta(days=today.weekday())
            starts = [mon - timedelta(weeks=span - 1 - i) for i in range(span)]
        else:
            first = today.replace(day=1)
            starts = []
            for i in range(span - 1, -1, -1):
                y, m = first.year, first.month - i
                while m <= 0:
                    m += 12
                    y -= 1
                starts.append(date(y, m, 1))
        d0 = starts[0]
        M, Mem = self.env["otm.gym.member"], self.env["otm.gym.membership"]
        new = dict(M._read_group([("join_date", ">=", d0)], ["join_date:%s" % g], ["__count"]))
        ren = dict(Mem._read_group([("renewal_of_id", "!=", False), ("state", "not in", ("draft", "cancelled")),
                                    ("start_date", ">=", d0)], ["start_date:%s" % g], ["__count"]))
        exp = dict(Mem._read_group([("state", "=", "expired"), ("end_date", ">=", d0)], ["end_date:%s" % g], ["__count"]))
        fmt = {"day": "%d %b", "week": "%d %b", "month": "%b %y"}[g]
        return {"granularity": granularity, "labels": [s.strftime(fmt) for s in starts], "series": [
            {"key": "new", "label": "New Members", "values": [new.get(s, 0) for s in starts]},
            {"key": "renewals", "label": "Renewals", "values": [ren.get(s, 0) for s in starts]},
            {"key": "expired", "label": "Expired", "values": [exp.get(s, 0) for s in starts]}]}

    @api.model
    def get_trainer_summary(self):
        today = self._today()
        Tr, Ap, Me = self.env["otm.gym.trainer"], self.env["otm.gym.appointment"], self.env["otm.gym.member"]
        trainers = Tr.search([("status", "=", "active")])
        members = dict(Me._read_group([("trainer_id", "in", trainers.ids), ("status", "=", "active")], ["trainer_id"], ["__count"]))
        sessions = {}
        for tr, st, n in Ap._read_group([("date", "=", today), ("trainer_id", "in", trainers.ids), ("state", "!=", "cancelled")],
                                        ["trainer_id", "state"], ["__count"]):
            sessions.setdefault(tr.id, {})[st] = n
        rows = []
        for t in trainers:
            s = sessions.get(t.id, {})
            rows.append({"id": t.id, "trainer": t.name, "role": t.staff_role, "members": members.get(t, 0),
                         "today": sum(s.values()), "completed": s.get("completed", 0),
                         "pending": s.get("scheduled", 0) + s.get("confirmed", 0) + s.get("in_progress", 0)})
        return sorted(rows, key=lambda r: -r["members"])

    @api.model
    def get_membership_distribution(self):
        rows = self.env["otm.gym.membership"]._read_group([("state", "in", LIVE)], ["plan_id"], ["__count"])
        return [{"key": str(p.id), "label": p.name, "value": n} for p, n in rows]

    @api.model
    def get_notifications(self, limit=30):
        N = self.env["otm.gym.notification"].sudo()
        dom = N._gym_domain_for(self.env.user) + [("date", ">=", fields.Datetime.now() - timedelta(days=30))]
        labels = dict(N._fields["ntype"].selection)
        return [{"id": n.id, "date": str(n.date), "res_model": n.res_model or "", "res_id": n.res_id or 0,
                 "title": n.title, "by": labels.get(n.ntype, n.ntype), "action": n.ntype, "from": "", "to": "",
                 "reason": n.body or ""} for n in N.search(dom, limit=limit)]

    @api.model
    def _quick_actions(self):
        defs = [("add_member", "Add Member", "/members/new", "otm.gym.member", "user-plus"),
                ("new_membership", "New Membership", "/memberships/new", "otm.gym.membership", "id-card"),
                ("check_in", "Check In", "#checkin", "otm.gym.attendance", "log-in"),
                ("book_training", "Book Training", "/appointments/new", "otm.gym.appointment", "calendar-plus"),
                ("assessment", "Assessment", "/assessments/new", "otm.gym.assessment", "ruler"),
                ("workout", "Workout Plan", "/workouts/new", "otm.gym.workout.plan", "dumbbell"),
                ("diet", "Diet Plan", "/diets/new", "otm.gym.diet.plan", "apple"),
                ("payment", "Payment", "/payments/new", "otm.gym.payment", "wallet")]
        return [{"key": k, "label": l, "href": h, "icon": i} for k, l, h, m, i in defs if self.env[m].has_access("create")]

    # ----------------------------------------------------------- main payload
    @api.model
    def get_dashboard(self, filters=None):
        filters = filters or {}
        role = self._role()
        if not role:
            raise AccessError(_("You do not have a Gym role."))
        user = self.env.user
        today = self._today()
        data = {"role": role, "title": "Gym Town", "currency": self.env.company.currency_id.symbol or "",
                "greeting": self._greeting(), "today": str(today),
                "user": {"id": user.id, "name": user.name, "first_name": (user.name or "").split(" ")[0]}}
        if role == "customer":
            data["customer"] = self.get_customer_home()
            data["notifications"] = self.get_notifications(10)
            return data
        wide = role in ("admin", "manager")
        ops = wide or role == "reception"
        M, Att, Ap, Ms, Pay = (self.env[x] for x in ("otm.gym.member", "otm.gym.attendance", "otm.gym.appointment",
                                                      "otm.gym.membership", "otm.gym.payment"))
        week = today + timedelta(days=7)
        exp7 = [("state", "in", LIVE), ("end_date", ">=", today), ("end_date", "<=", week)]
        open_pay = [("state", "in", ("pending", "requested", "partial"))]
        due_assess = [("status", "=", "active"), "|", ("last_assessment_date", "=", False),
                      ("last_assessment_date", "<", today - timedelta(days=30))]
        due_assess_s = [("status", "=", "active"), "|", ("last_assessment_date", "=", False),
                        ("last_assessment_date", "<", str(today - timedelta(days=30)))]
        # KPI builders are lazy: a role only runs the queries on models it is allowed to read
        B = {
            "total": lambda: self._kpi("total_members", "Total Members", M.search_count([]), "idle", "otm.gym.member", [], "All members"),
            "active": lambda: self._kpi("active_members", "Active Members", M.search_count([("status", "=", "active")]), "good",
                                        "otm.gym.member", [("status", "=", "active")], "Active members"),
            "new": lambda: self._kpi("new_members", "New Members", M.search_count([("join_date", ">=", today.replace(day=1))]), "accent",
                                     "otm.gym.member", [("join_date", ">=", str(today.replace(day=1)))], "New this month"),
            "checkins": lambda: self._kpi("todays_checkins", "Today's Check-ins",
                                          Att.search_count([("date", "=", today), ("state", "in", PRESENT)]), "accent", "otm.gym.attendance",
                                          [("date", "=", str(today)), ("state", "in", list(PRESENT))], "Today's check-ins"),
            "inside": lambda: self._kpi("currently_inside", "Currently Inside", Att.search_count([("state", "=", "present")]), "accent",
                                        "otm.gym.attendance", [("state", "=", "present")], "Currently inside"),
            "expiring": lambda: self._kpi("expiring_memberships", "Expiring Memberships", Ms.search_count(exp7), "warn",
                                          "otm.gym.membership", [("state", "in", list(LIVE)), ("end_date", ">=", str(today)),
                                                                 ("end_date", "<=", str(week))], "Expiring in 7 days"),
            "pending_pay": lambda: self._kpi("pending_payments", "Pending Payments", Pay.search_count(open_pay), "warn", "otm.gym.payment",
                                             [("state", "in", ["pending", "requested", "partial"])], "Pending payments"),
            "revenue": lambda: self._kpi("todays_revenue", "Today's Revenue", data["revenue"]["totals"]["today"], "accent",
                                         "otm.gym.payment.receipt", [("date", "=", str(today))], "Today's receipts", kind="sum"),
            "outstanding": lambda: self._kpi("outstanding", "Outstanding", Pay._read_group(open_pay, [], ["balance:sum"])[0][0] or 0.0, "bad",
                                             "otm.gym.payment", [("state", "in", ["pending", "requested", "partial"])], "Outstanding", kind="sum"),
            "my_members": lambda: self._kpi("my_members", "My Members", M.search_count([("status", "=", "active")]), "accent",
                                            "otm.gym.member", [("status", "=", "active")], "My members"),
            "sessions": lambda: self._kpi("todays_sessions", "Today's Sessions",
                                          Ap.search_count([("date", "=", today), ("state", "!=", "cancelled")]), "accent",
                                          "otm.gym.appointment", [("date", "=", str(today))], "Today's sessions"),
            "assess_due": lambda: self._kpi("assessments_due", "Assessments Due", M.search_count(due_assess), "warn", "otm.gym.member",
                                            due_assess_s, "Assessments due"),
        }
        cache = {}

        def kv(key):
            if key not in cache:
                cache[key] = B[key]()
            return cache[key]

        if self._is("finance", "manager"):
            data["revenue"] = self.get_revenue_summary()
        order = {
            "admin": ["total", "active", "new", "checkins", "inside", "expiring", "pending_pay", "revenue"],
            "manager": ["total", "active", "new", "checkins", "inside", "expiring", "pending_pay", "revenue"],
            "reception": ["total", "active", "new", "checkins", "inside", "expiring", "pending_pay"],
            "finance": ["pending_pay", "revenue", "outstanding", "expiring"],
            "trainer": ["my_members", "sessions", "assess_due", "inside"],
            "nutritionist": ["my_members", "assess_due"],
            "assessor": ["sessions", "assess_due"],
        }[role]
        if "revenue" in order and "revenue" not in data:
            order = [x for x in order if x != "revenue"]
        data["kpis"] = [kv(x) for x in order]
        data["quick_actions"] = self._quick_actions()
        data["notifications"] = self.get_notifications(8)
        # attendance + people panels
        if ops or role in ("trainer",):
            inside = self.get_currently_inside()
            present_n = Att._read_group([("date", "=", today), ("state", "in", PRESENT)], [], ["member_id:count_distinct"])[0][0]
            data["attendance"] = self.get_attendance_trend(filters.get("range") or "7d", filters.get("date_from"), filters.get("date_to"))
            data["members_present"] = self.get_members_present()
            data["currently_inside"] = inside
            data["today_panel"] = [
                {"key": "present", "label": "Members Present", "value": present_n},
                {"key": "inside", "label": "Currently Inside", "value": inside["count"]},
                {"key": "pt", "label": "Personal Training Sessions", "value": Ap.search_count(
                    [("date", "=", today), ("appointment_type", "=", "personal_training"), ("state", "!=", "cancelled")])},
                {"key": "assessments", "label": "Assessments", "value": Ap.search_count(
                    [("date", "=", today), ("appointment_type", "=", "assessment"), ("state", "!=", "cancelled")])},
                {"key": "expiring", "label": "Expiring Memberships", "value": Ms.search_count(exp7)}]
            if self._is("finance", "manager"):
                data["today_panel"].append({"key": "collection", "label": "Today's Collection", "value": data["revenue"]["totals"]["today"],
                                            "kind": "sum"})
        if ops or role in ("trainer", "assessor", "nutritionist"):
            data["sessions"] = self.get_today_sessions()
        if ops or role == "finance":
            data["expiring_memberships"] = self.get_membership_expiry(filters.get("expiry_window", 30))
        charts = {}
        if wide:
            charts["member_growth"] = self.get_member_growth(filters.get("growth") or "daily")
            charts["trainer_workload"] = self.get_trainer_summary()
            data["trainer_summary"] = charts["trainer_workload"]
        if wide or role in ("reception", "finance"):
            charts["membership_distribution"] = self.get_membership_distribution()
            charts["membership_expiry"] = [
                {"key": "%s-%s" % (a, b), "label": "%s-%s d" % (a, b), "value": Ms.search_count(
                    [("state", "in", LIVE), ("end_date", ">=", today + timedelta(days=a)), ("end_date", "<=", today + timedelta(days=b))])}
                for a, b in ((0, 7), (8, 15), (16, 30), (31, 60))]
        if self._is("finance", "manager"):
            charts["revenue"] = data["revenue"]["rows"]
        data["charts"] = charts
        # personal work panels
        if role in ("trainer", "nutritionist", "assessor"):
            data["role_panels"] = self._role_panels(role, today, due_assess)
        # good-morning summary: values come only from the queries above
        summary = []
        if role != "finance":
            summary.append({"key": "sessions", "label": "sessions today", "value": kv("sessions")["value"] if "sessions" in order else
                            Ap.search_count([("date", "=", today), ("state", "!=", "cancelled")])})
            summary.append({"key": "assess", "label": "assessments pending", "value": kv("assess_due")["value"] if "assess_due" in order
                            else M.search_count(due_assess)})
        if ops or role == "finance":
            summary.append({"key": "expiring", "label": "memberships expiring this week", "value": kv("expiring")["value"]})
        if ops or role == "trainer":
            summary.append({"key": "inside", "label": "members currently inside", "value": kv("inside")["value"]})
        data["summary"] = [x for x in summary if x["value"]]
        return data

    @api.model
    def _role_panels(self, role, today, due_assess):
        M, Ap, Wp, Dp = (self.env[x] for x in ("otm.gym.member", "otm.gym.appointment", "otm.gym.workout.plan", "otm.gym.diet.plan"))
        panels = {}

        def members(domain, limit=8):
            rows = M.search_read(domain, ["name", "member_code", "status", "fitness_goal", "membership_expiry"], limit=limit, order="name")
            ph = self._photo_map("otm.gym.member", [r["id"] for r in rows])
            return [dict(r, photo=ph.get(r["id"]), membership_expiry=str(r["membership_expiry"] or "")) for r in rows]

        if role in ("trainer", "nutritionist"):
            panels["my_members"] = {"count": M.search_count([("status", "=", "active")]), "rows": members([("status", "=", "active")])}
            panels["assessments_due"] = {"count": M.search_count(due_assess), "rows": members(due_assess, 6)}
        if role in ("trainer", "assessor", "nutritionist"):
            up = Ap.search_read([("date", ">", today), ("date", "<=", today + timedelta(days=7)),
                                 ("state", "in", ("scheduled", "confirmed"))],
                                ["member_id", "appointment_type", "start_datetime", "state"], order="start_datetime asc", limit=8)
            panels["upcoming"] = [{"id": r["id"], "member": r["member_id"][1], "member_id": r["member_id"][0],
                                   "type": TYPE_LABEL.get(r["appointment_type"]), "when": str(self._local(r["start_datetime"]))[:16],
                                   "state": r["state"]} for r in up]
        if role == "trainer":
            panels["workout_plans"] = {s: Wp.search_count([("state", "=", s)]) for s in ("active", "review", "draft")}
            panels["diet_plans"] = {s: Dp.search_count([("state", "=", s)]) for s in ("active", "review", "draft")}
            panels["progress"] = [self._progress_delta(r) for r in members([("status", "=", "active")], 5)]
        if role == "nutritionist":
            panels["diet_plans"] = {s: Dp.search_count([("state", "=", s)]) for s in ("active", "review", "draft")}
        if role == "assessor":
            rows = self.env["otm.gym.assessment"].search_read([], ["member_id", "date", "state", "weight", "bmi"], limit=8,
                                                              order="date desc, id desc")
            panels["recent_assessments"] = [dict(r, member=r["member_id"][1], date=str(r["date"])) for r in rows]
        return panels

    @api.model
    def _progress_delta(self, member_row):
        p = self.get_progress(member_row["id"])
        w = (p.get("series") or {}).get("weight") or []
        w = [x for x in w if x]
        return {"member_id": member_row["id"], "name": member_row["name"], "photo": member_row.get("photo"),
                "from": w[0] if w else None, "to": w[-1] if w else None,
                "delta": round(w[-1] - w[0], 1) if len(w) > 1 else None}

    # -------------------------------------------------------------- progress
    @api.model
    def get_progress(self, member_id):
        """Weight / BMI / body fat / ... history from completed assessments + progress logs (history is never overwritten)."""
        member_id = int(member_id)
        try:
            ass = self.env["otm.gym.assessment"].search_read(
                [("member_id", "=", member_id), ("state", "=", "done")],
                ["date", "weight", "bmi", "body_fat", "muscle_mass", "chest", "waist", "hip", "arm", "thigh"], order="date asc, id asc")
            logs = self.env["otm.gym.progress"].search_read(
                [("member_id", "=", member_id)], ["date", "weight", "bmi", "body_fat", "muscle_mass", "chest", "waist", "hip", "arm", "thigh"],
                order="date asc, id asc")
        except AccessError:
            return {"restricted": True}
        pts = sorted(ass + logs, key=lambda r: (r["date"], r["id"]))
        keys = ["weight", "bmi", "body_fat", "muscle_mass", "chest", "waist", "hip", "arm", "thigh"]
        return {"labels": [p["date"].strftime("%d %b %y") for p in pts], "dates": [str(p["date"]) for p in pts],
                "series": {k: [p[k] or None for p in pts] for k in keys}}

    # --------------------------------------------------------- customer home
    @api.model
    def _my_member(self):
        return self.env["otm.gym.member"].search([("user_id", "=", self.env.user.id)], limit=1)

    @api.model
    def get_customer_home(self):
        m = self._my_member()
        if not m:
            return {"found": False}
        today = self._today()
        ms = m.current_membership_id
        out = {"found": True, "member": {"id": m.id, "name": m.name, "code": m.member_code, "status": m.status,
                                          "photo": self._photo_map("otm.gym.member", [m.id]).get(m.id),
                                          "goal": m.fitness_goal and dict(m._fields["fitness_goal"].selection).get(m.fitness_goal),
                                          "trainer": m.trainer_id.name or ""},
               "membership": {"plan": ms.plan_id.name or "", "valid_until": str(ms.end_date or ""), "state": ms.state or "",
                              "days_left": (ms.end_date - today).days if ms.end_date else None} if ms else None}
        # today's workout
        out["workout"] = None
        plan = self.env["otm.gym.workout.plan"].search([("member_id", "=", m.id), ("state", "=", "active")], limit=1)
        if plan:
            day = DAY_KEYS[today.weekday()]
            lines = plan.line_ids.filtered(lambda l: l.day == day)
            out["workout"] = {"plan": plan.name, "version": plan.version, "title": (lines[:1].day_title or "") if lines else "",
                              "rest_day": not lines, "exercises": [{"name": l.exercise_id.name, "sets": l.sets, "reps": l.reps,
                                                                    "rest": l.rest, "weight": l.weight, "notes": l.notes or ""} for l in lines]}
        # today's diet
        out["diet"] = None
        diet = self.env["otm.gym.diet.plan"].search([("member_id", "=", m.id), ("state", "=", "active")], limit=1)
        if diet:
            meals = {}
            for l in diet.line_ids:
                meals.setdefault(l.meal_type, []).append({"food": l.food_id.name, "qty": l.quantity, "calories": round(l.calories)})
            labels = dict(self.env["otm.gym.diet.plan.line"]._fields["meal_type"].selection)
            out["diet"] = {"plan": diet.name, "calories": round(diet.calories_total), "target": diet.calories_target,
                           "meals": [{"meal": labels[k], "items": v} for k, v in meals.items()]}
        first = today.replace(day=1)
        out["attendance"] = {"visits_month": self.env["otm.gym.attendance"].search_count(
            [("member_id", "=", m.id), ("date", ">=", first), ("state", "in", PRESENT)]),
            "inside": bool(self.env["otm.gym.attendance"].search_count([("member_id", "=", m.id), ("state", "=", "present")]))}
        prog = self.get_progress(m.id)
        w = [x for x in (prog.get("series") or {}).get("weight", []) if x]
        out["progress"] = {"from": w[0] if w else None, "to": w[-1] if w else None, "series": prog}
        nxt = self.env["otm.gym.appointment"].search([("member_id", "=", m.id), ("start_datetime", ">=", fields.Datetime.now()),
                                                      ("state", "in", ("scheduled", "confirmed"))], order="start_datetime asc", limit=1)
        out["next_session"] = {"when": str(self._local(nxt.start_datetime))[:16], "type": TYPE_LABEL.get(nxt.appointment_type),
                               "trainer": nxt.trainer_id.name} if nxt else None
        owed = self.env["otm.gym.payment"]._read_group(
            [("member_id", "=", m.id), ("state", "in", ("pending", "requested", "partial"))], [], ["balance:sum"])[0][0] or 0.0
        out["outstanding"] = owed
        out["currency"] = self.env.company.currency_id.symbol or ""
        return out

    # ------------------------------------------------------------ Member 360
    @api.model
    def get_member_360(self, member_id):
        member = self.env["otm.gym.member"].browse(int(member_id)).exists()
        if not member:
            raise UserError(_("Member not found."))
        member.check_access("read")
        today = self._today()
        Sel = lambda model, f, v: dict(self.env[model]._fields[f].selection).get(v, v) if v else ""

        def safe(fn):
            try:
                return fn()
            except AccessError:
                return {"restricted": True}

        def rows(model, domain, fields_, order="id desc", limit=20):
            res = self.env[model].search_read(domain, fields_, order=order, limit=limit)
            return res

        ms = member.current_membership_id
        out = {"header": {
            "id": member.id, "name": member.name, "code": member.member_code, "status": member.status,
            "photo": self._photo_map("otm.gym.member", [member.id]).get(member.id),
            "membership": ms.plan_id.name or "", "membership_state": ms.state or "", "expiry": str(member.membership_expiry or ""),
            "days_left": (member.membership_expiry - today).days if member.membership_expiry else None,
            "trainer": member.trainer_id.name or "", "goal": Sel("otm.gym.member", "fitness_goal", member.fitness_goal),
            "phone": member.phone or "", "inside": bool(self.env["otm.gym.attendance"].sudo().search_count(
                [("member_id", "=", member.id), ("state", "=", "present")]))}}
        out["membership"] = safe(lambda: rows("otm.gym.membership", [("member_id", "=", member.id)],
                                              ["name", "plan_id", "start_date", "end_date", "final_amount", "payment_status", "state", "renewal_stage"]))
        out["attendance"] = safe(lambda: {
            "recent": rows("otm.gym.attendance", [("member_id", "=", member.id), ("state", "in", PRESENT)],
                           ["date", "check_in", "check_out", "duration", "source"], order="check_in desc", limit=15),
            "visits_30d": self.env["otm.gym.attendance"].search_count(
                [("member_id", "=", member.id), ("date", ">=", today - timedelta(days=30)), ("state", "in", PRESENT)]),
            "last_visit": str(member.last_attendance_date or "")})
        out["health"] = safe(lambda: (self.env["otm.gym.health.profile"].search_read(
            [("member_id", "=", member.id)], ["height", "weight", "bmi", "bmi_category", "body_fat", "muscle_mass", "blood_pressure",
                                               "allergies", "injuries", "medical_restrictions", "fitness_limitations", "emergency_information", "notes"], limit=1) or [None])[0])
        out["assessment"] = safe(lambda: rows("otm.gym.assessment", [("member_id", "=", member.id)],
                                              ["name", "date", "weight", "bmi", "body_fat", "muscle_mass", "waist", "state"], order="date desc, id desc"))
        out["workout"] = safe(lambda: rows("otm.gym.workout.plan", [("member_id", "=", member.id)], ["name", "version", "goal", "start_date", "end_date", "state"]))
        out["diet"] = safe(lambda: rows("otm.gym.diet.plan", [("member_id", "=", member.id)],
                                        ["name", "version", "calories_target", "calories_total", "start_date", "end_date", "state"]))
        out["appointments"] = safe(lambda: rows("otm.gym.appointment", [("member_id", "=", member.id)],
                                                ["name", "appointment_type", "start_datetime", "trainer_id", "state"], order="start_datetime desc"))
        out["payments"] = safe(lambda: rows("otm.gym.payment", [("member_id", "=", member.id)],
                                            ["name", "kind", "description", "amount", "paid_amount", "balance", "due_date", "state"]))
        out["progress"] = safe(lambda: self.get_progress(member.id))
        out["documents"] = safe(lambda: self.env["ir.attachment"].search_read(
            [("res_model", "=", "otm.gym.member"), ("res_id", "=", member.id), ("res_field", "=", False)],
            ["name", "mimetype", "create_date", "file_size"], order="id desc", limit=30))
        out["notes"] = {"notes": member.notes or "", "emergency": [member.emergency_contact or "", member.emergency_phone or ""]}
        out["overview"] = {"phone": member.phone or "", "whatsapp": member.whatsapp or "", "email": member.email or "",
                           "age": member.age, "join_date": str(member.join_date or ""), "outstanding": member.outstanding_amount,
                           "memberships": member.membership_count, "visits": member.attendance_count,
                           "last_assessment": str(member.last_assessment_date or "")}
        out["currency"] = self.env.company.currency_id.symbol or ""
        return out
