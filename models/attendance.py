from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

SOURCES = [("reception", "Reception"), ("qr", "QR"), ("rfid", "RFID"), ("mobile", "Mobile"),
           ("portal", "Portal"), ("manual", "Manual")]


class GymAttendance(models.Model):
    _name = "otm.gym.attendance"
    _description = "Gym Attendance"
    _inherit = ["otm.gym.workflow.mixin"]
    _order = "check_in desc, id desc"

    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="cascade")
    membership_id = fields.Many2one("otm.gym.membership", string="Membership used", readonly=True)
    check_in = fields.Datetime(index=True)
    check_out = fields.Datetime()
    date = fields.Date(compute="_compute_date", store=True, readonly=False, index=True)
    duration = fields.Float(string="Duration (h)", compute="_compute_duration", store=True)
    trainer_id = fields.Many2one("otm.gym.trainer", string="Trainer")
    source = fields.Selection(SOURCES, default="reception", required=True)
    state = fields.Selection([("expected", "Expected"), ("present", "Present"), ("checked_out", "Checked Out"),
                              ("absent", "Absent")], default="expected", required=True, index=True, copy=False)
    notes = fields.Char()
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def init(self):
        # at most ONE open (present) attendance per member, enforced by the database too
        self.env.cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS otm_gym_attendance_one_open "
                            "ON otm_gym_attendance (member_id) WHERE state = 'present'")

    # ------------------------------------------------------------ compute
    @api.depends("check_in")
    def _compute_date(self):
        for rec in self:
            if rec.check_in:
                rec.date = fields.Datetime.context_timestamp(rec, rec.check_in).date()
            else:
                rec.date = rec.date or fields.Date.context_today(rec)

    @api.depends("check_in", "check_out")
    def _compute_duration(self):
        for rec in self:
            rec.duration = (rec.check_out - rec.check_in).total_seconds() / 3600.0 if rec.check_in and rec.check_out else 0.0

    # -------------------------------------------------------------- guards
    def write(self, vals):
        if ("check_in" in vals or "check_out" in vals) and not self.env.su and not self.env.context.get("gym_state_write"):
            self._gym_check_groups(["manager"])
            res = super().write(vals)
            for rec in self:
                rec._gym_log("attendance_correction", None, None, _("Times corrected by %s", self.env.user.name))
            return res
        return super().write(vals)

    def unlink(self):
        if not self.env.su and not self._gym_has_group(["manager"]):
            raise AccessError(_("Only a manager can delete attendance."))
        return super().unlink()

    # --------------------------------------------------------- check in/out
    @api.model
    def _gym_resolve_member(self, member_id):
        try:
            member_id = int(member_id)
        except (TypeError, ValueError):
            raise UserError(_("Member not found."))
        member = self.env["otm.gym.member"].sudo().browse(member_id).exists()
        if not member:
            raise UserError(_("Member not found."))
        return member

    @api.model
    def _gym_check_actor(self, member, source):
        """Reception/manager can check anyone in; a customer only themselves."""
        user = self.env.user
        staff = self.env.su or user.has_group("otm_gym_management.group_gym_reception")
        if not staff and not (member.user_id == user and source in ("mobile", "portal", "qr")):
            raise AccessError(_("You are not allowed to check this member in."))

    @api.model
    def check_in_member(self, member_id, source="reception", trainer_id=False):
        member = self._gym_resolve_member(member_id)
        self._gym_check_actor(member, source)
        today = fields.Date.context_today(self)
        if member.status == "inactive":
            raise UserError(_("%s is inactive.", member.name))
        ms = member.current_membership_id
        live = member.membership_ids.filtered(
            lambda m: m.state in ("active", "renewal_pending") and m.start_date <= today <= m.end_date)
        if not live:
            if ms and ms.state == "frozen":
                raise UserError(_("%s's membership is frozen.", member.name))
            if ms and ms.end_date and ms.end_date < today:
                raise UserError(_("%(m)s's membership expired on %(d)s.", m=member.name, d=ms.end_date))
            raise UserError(_("%s has no active membership.", member.name))
        open_row = self.sudo().search([("member_id", "=", member.id), ("state", "=", "present")], limit=1)
        if open_row:
            t = fields.Datetime.context_timestamp(self, open_row.check_in).strftime("%I:%M %p")
            raise UserError(_("%(m)s is already checked in (since %(t)s).", m=member.name, t=t))
        now = fields.Datetime.now()
        vals = {"member_id": member.id, "check_in": now, "source": source, "membership_id": live[0].id,
                "trainer_id": trainer_id and int(trainer_id) or member.trainer_id.id, "state": "present"}
        expected = self.sudo().search([("member_id", "=", member.id), ("state", "=", "expected"), ("date", "=", today)], limit=1)
        if expected:
            expected.with_context(gym_state_write=True).write(vals)
            row = expected
        else:
            row = self.sudo().with_context(gym_state_write=True).create(vals)
        member.sudo().write({"last_attendance_date": today})
        row._gym_log("check_in", "expected" if expected else None, "present")
        return {"id": row.id, "member": member.name, "code": member.member_code,
                "check_in": fields.Datetime.to_string(now), "message": _("%s checked in.", member.name)}

    @api.model
    def check_in_by_code(self, code, source="rfid"):
        code = (code or "").strip()
        if not code:
            raise UserError(_("Scan or type a member code."))
        member = self.env["otm.gym.member"].sudo().search(
            ["|", "|", ("member_code", "=ilike", code), ("rfid_code", "=", code), ("qr_token", "=", code)], limit=1)
        if not member:
            raise UserError(_("No member matches the code \"%s\".", code))
        return self.check_in_member(member.id, source)

    @api.model
    def check_out_member(self, member_id):
        member = self._gym_resolve_member(member_id)
        self._gym_check_actor(member, "portal")
        row = self.sudo().search([("member_id", "=", member.id), ("state", "=", "present")], limit=1)
        if not row:
            raise UserError(_("%s is not checked in.", member.name))
        row.sudo().with_context(gym_state_write=True).write({"check_out": fields.Datetime.now(), "state": "checked_out"})
        row._gym_log("check_out", "present", "checked_out")
        return {"id": row.id, "message": _("%s checked out.", member.name)}

    def action_check_out(self, reason=None, **kw):
        return self._gym_move("action_check_out", reason, vals={"check_out": fields.Datetime.now()})

    def action_mark_absent(self, reason=None, **kw):
        return self._gym_move("action_mark_absent", reason)
