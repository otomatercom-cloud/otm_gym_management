from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

TYPES = [("personal_training", "Personal Training"), ("assessment", "Fitness Assessment"),
         ("diet_consultation", "Diet Consultation"), ("fitness_consultation", "Fitness Consultation"),
         ("other", "Other")]
ACTIVE_STATES = ("scheduled", "confirmed", "in_progress", "completed")


class GymAppointment(models.Model):
    _name = "otm.gym.appointment"
    _description = "Gym Appointment / Session"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _order = "start_datetime desc, id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="cascade", tracking=True)
    trainer_id = fields.Many2one("otm.gym.trainer", string="Trainer / Staff", required=True, index=True, tracking=True)
    appointment_type = fields.Selection(TYPES, string="Session type", default="personal_training", required=True, index=True)
    start_datetime = fields.Datetime(string="Start", required=True, index=True, tracking=True)
    duration_minutes = fields.Integer(string="Duration (min)", default=60, required=True)
    stop_datetime = fields.Datetime(string="End", compute="_compute_stop", store=True, index=True)
    date = fields.Date(compute="_compute_date", store=True, index=True)
    state = fields.Selection([("scheduled", "Scheduled"), ("confirmed", "Confirmed"), ("in_progress", "In Progress"),
                              ("completed", "Completed"), ("cancelled", "Cancelled"), ("no_show", "No Show")],
                             default="scheduled", required=True, index=True, tracking=True, copy=False)
    notes = fields.Text()
    payment_id = fields.Many2one("otm.gym.payment", string="Payment charge", copy=False)
    assessment_id = fields.Many2one("otm.gym.assessment", string="Assessment", copy=False, readonly=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("start_datetime", "duration_minutes")
    def _compute_stop(self):
        for rec in self:
            rec.stop_datetime = rec.start_datetime and rec.start_datetime + timedelta(minutes=rec.duration_minutes or 0)

    @api.depends("start_datetime")
    def _compute_date(self):
        for rec in self:
            rec.date = rec.start_datetime and fields.Datetime.context_timestamp(rec, rec.start_datetime).date() or False

    @api.constrains("duration_minutes")
    def _check_duration(self):
        for rec in self:
            if rec.duration_minutes <= 0:
                raise ValidationError(_("The duration must be greater than zero."))

    @api.constrains("trainer_id", "start_datetime", "stop_datetime", "state")
    def _check_double_booking(self):
        for rec in self.filtered(lambda r: r.state in ACTIVE_STATES and r.start_datetime):
            clash = self.sudo().search([("id", "!=", rec.id), ("trainer_id", "=", rec.trainer_id.id),
                                        ("state", "in", ACTIVE_STATES),
                                        ("start_datetime", "<", rec.stop_datetime),
                                        ("stop_datetime", ">", rec.start_datetime)], limit=1)
            if clash:
                raise ValidationError(_("%(t)s is already booked at that time (%(r)s).", t=rec.trainer_id.name, r=clash.name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.appointment") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if {"trainer_id", "start_datetime", "duration_minutes", "member_id"} & set(vals) and not self.env.su:
            if self.filtered(lambda r: r.state in ("in_progress", "completed", "cancelled", "no_show")):
                raise UserError(_("Started or closed sessions cannot be rescheduled."))
        res = super().write(vals)
        if "trainer_id" in vals:
            for rec in self:
                rec._gym_log("trainer_changed", None, rec.trainer_id.name)
        return res

    # ------------------------------------------------------------ actions
    def action_confirm(self, reason=None, **kw):
        return self._gym_move("action_confirm", reason)

    def action_start(self, reason=None, **kw):
        return self._gym_move("action_start", reason)

    def action_complete(self, reason=None, **kw):
        return self._gym_move("action_complete", reason)

    def action_cancel(self, reason=None, **kw):
        return self._gym_move("action_cancel", reason)

    def action_no_show(self, reason=None, **kw):
        return self._gym_move("action_no_show", reason)

    def action_create_assessment(self, **kw):
        self.ensure_one()
        self._gym_check_groups(["assessor", "trainer"])
        if not self.assessment_id:
            a = self.env["otm.gym.assessment"].create({"member_id": self.member_id.id, "appointment_id": self.id,
                                                       "date": self.date or fields.Date.context_today(self)})
            self.with_context(gym_state_write=True).write({"assessment_id": a.id})
        return {"res_model": "otm.gym.assessment", "res_id": self.assessment_id.id}
