import logging
from datetime import timedelta

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class GymCron(models.AbstractModel):
    """Scheduled jobs. Every notification carries a dedupe key so reruns never duplicate."""
    _name = "otm.gym.cron"
    _description = "Gym scheduled jobs"

    @api.model
    def _param_days(self, key, default):
        raw = self.env["ir.config_parameter"].sudo().get_param("otm_gym.%s" % key, default)
        out = []
        for part in str(raw).replace(";", ",").split(","):
            try:
                out.append(int(part.strip()))
            except ValueError:
                continue
        return out or [int(x) for x in str(default).split(",")]

    # ------------------------------------------------------------ daily
    @api.model
    def cron_membership_lifecycle(self):
        today = fields.Date.context_today(self)
        Membership = self.env["otm.gym.membership"]
        N = self.env["otm.gym.notification"]
        # 1. auto-unfreeze
        for m in Membership.search([("state", "=", "frozen"), ("freeze_end", "<=", today)]):
            m.action_unfreeze()
        # 2. expire memberships whose end date passed
        for m in Membership.search([("state", "in", ("active", "renewal_pending")), ("end_date", "<", today)]):
            m.action_expire()
        # 3. reminders at 30/15/7/3/1 days (configurable)
        for days in self._param_days("reminder_days", "30,15,7,3,1"):
            target = today + timedelta(days=days)
            for m in Membership.search([("state", "in", ("active", "renewal_pending")), ("end_date", "=", target)]):
                if m.renewal_stage == "none":
                    m.with_context(gym_state_write=True).write({"renewal_stage": "expiring"})
                N._gym_notify(
                    "membership_expiring", _("Membership expiring in %(d)s day(s): %(m)s", d=days, m=m.member_id.name),
                    _("%(p)s ends on %(e)s.", p=m.plan_id.name, e=m.end_date), member=m.member_id, rec=m,
                    groups=["reception", "manager"], users=m.member_id.user_id | m.trainer_id.user_id,
                    key=f"exp:{m.id}:{days}")
                if days <= 7:
                    N._gym_notify("renewal_reminder", _("Renew %s", m.member_id.name),
                                  _("Renewal follow-up needed (%s day(s) left).", days), member=m.member_id, rec=m,
                                  groups=["reception"], key=f"ren:{m.id}:{days}")

    @api.model
    def cron_payments_overdue(self):
        today = fields.Date.context_today(self)
        N = self.env["otm.gym.notification"]
        for p in self.env["otm.gym.payment"].search([("state", "in", ("requested", "partial")), ("due_date", "<", today)]):
            N._gym_notify("payment_overdue", _("Payment overdue: %s", p.member_id.name),
                          _("%(n)s - %(b)s outstanding since %(d)s.", n=p.name, b=p.balance, d=p.due_date),
                          member=p.member_id, rec=p, groups=["finance", "manager"], users=p.member_id.user_id,
                          key=f"overdue:{p.id}:{today.isocalendar()[1]}:{today.year}")

    @api.model
    def cron_assessments_due(self):
        today = fields.Date.context_today(self)
        days = self._param_days("assessment_interval_days", "30")[0]
        limit = today - timedelta(days=days)
        N = self.env["otm.gym.notification"]
        due = self.env["otm.gym.member"].search([("status", "=", "active"), "|", ("last_assessment_date", "=", False),
                                                  ("last_assessment_date", "<", limit)])
        for m in due:
            N._gym_notify("assessment_due", _("Assessment due: %s", m.name),
                          _("No fitness assessment in the last %s days.", days), member=m, groups=["manager"],
                          users=m.trainer_id.user_id, key=f"assess:{m.id}:{today.year}-{today.month}")

    @api.model
    def cron_plan_reviews(self):
        today = fields.Date.context_today(self)
        N = self.env["otm.gym.notification"]
        for plan in self.env["otm.gym.workout.plan"].search([("state", "=", "active"), ("end_date", "!=", False),
                                                              ("end_date", "<=", today + timedelta(days=7))]):
            N._gym_notify("trainer_session", _("Workout plan ending: %s", plan.member_id.name),
                          _("%(n)s ends on %(d)s - review it.", n=plan.name, d=plan.end_date), member=plan.member_id, rec=plan,
                          users=plan.trainer_id.user_id, key=f"wreview:{plan.id}")
        for plan in self.env["otm.gym.diet.plan"].search([("state", "=", "active"), ("end_date", "!=", False),
                                                           ("end_date", "<=", today + timedelta(days=7))]):
            N._gym_notify("diet_updated", _("Diet plan ending: %s", plan.member_id.name),
                          _("%(n)s ends on %(d)s - review it.", n=plan.name, d=plan.end_date), member=plan.member_id, rec=plan,
                          users=plan.nutritionist_id, key=f"dreview:{plan.id}")

    @api.model
    def cron_long_absence(self):
        today = fields.Date.context_today(self)
        days = self._param_days("long_absence_days", "14")[0]
        N = self.env["otm.gym.notification"]
        absent = self.env["otm.gym.member"].search([("status", "=", "active"), ("last_attendance_date", "!=", False),
                                                     ("last_attendance_date", "<", today - timedelta(days=days))])
        for m in absent:
            N._gym_notify("long_absence", _("Long absence: %s", m.name),
                          _("Last visit on %s.", m.last_attendance_date), member=m, groups=["reception"],
                          users=m.trainer_id.user_id, key=f"absent:{m.id}:{m.last_attendance_date}")

    # ----------------------------------------------------------- frequent
    @api.model
    def cron_appointments(self):
        now = fields.Datetime.now()
        N = self.env["otm.gym.notification"]
        Att = self.env["otm.gym.attendance"].sudo()
        for a in self.env["otm.gym.appointment"].search([("state", "in", ("scheduled", "confirmed")),
                                                          ("start_datetime", ">", now),
                                                          ("start_datetime", "<=", now + timedelta(hours=24))]):
            N._gym_notify("appointment_reminder", _("Session tomorrow: %s", a.member_id.name),
                          _("%(t)s with %(s)s.", t=fields.Datetime.context_timestamp(a, a.start_datetime).strftime("%d %b %H:%M"),
                            s=a.trainer_id.name), member=a.member_id, rec=a, groups=["reception"],
                          users=a.member_id.user_id | a.trainer_id.user_id, key=f"apt24:{a.id}")
            N._gym_notify("trainer_session", _("Upcoming session: %s", a.member_id.name), a.appointment_type,
                          member=a.member_id, rec=a, users=a.trainer_id.user_id, key=f"aptT:{a.id}")
            today = fields.Datetime.context_timestamp(a, a.start_datetime).date()
            if not Att.search_count([("member_id", "=", a.member_id.id), ("date", "=", today)]):
                Att.with_context(gym_state_write=True).create({"member_id": a.member_id.id, "date": today, "state": "expected",
                                                               "trainer_id": a.trainer_id.id, "source": "manual"})
        # sessions that were never started become no-shows the next day (system action)
        yesterday = now - timedelta(days=1)
        stale = self.env["otm.gym.appointment"].search([("state", "in", ("scheduled", "confirmed")), ("stop_datetime", "<", yesterday)])
        for a in stale:
            a.sudo()._gym_move("action_no_show", _("Not attended - closed automatically"))
        # expected attendance that never happened -> absent; forgotten check-outs (>14h) are closed
        for att in Att.search([("state", "=", "expected"), ("date", "<", fields.Date.context_today(self))]):
            att._gym_move("action_mark_absent")
        for att in Att.search([("state", "=", "present"), ("check_in", "<", now - timedelta(hours=14))]):
            att.with_context(gym_state_write=True).write({"state": "checked_out", "check_out": att.check_in + timedelta(hours=3)})
            att._gym_log("auto_check_out", "present", "checked_out", _("Forgotten check-out closed automatically (3h assumed)"))
