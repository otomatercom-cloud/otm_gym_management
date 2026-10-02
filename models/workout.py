from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .member import GOALS

DAYS = [("monday", "Monday"), ("tuesday", "Tuesday"), ("wednesday", "Wednesday"), ("thursday", "Thursday"),
        ("friday", "Friday"), ("saturday", "Saturday"), ("sunday", "Sunday")]
PLAN_STATES = [("draft", "Draft"), ("review", "Review"), ("approved", "Approved"), ("active", "Active"),
               ("completed", "Completed"), ("archived", "Archived")]


class GymWorkoutPlan(models.Model):
    _name = "otm.gym.workout.plan"
    _description = "Gym Workout Plan"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _gym_sensitive = True
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, tracking=True, ondelete="restrict")
    trainer_id = fields.Many2one("otm.gym.trainer", string="Trainer", tracking=True, index=True)
    goal = fields.Selection(GOALS)
    start_date = fields.Date(default=fields.Date.context_today)
    end_date = fields.Date()
    version = fields.Integer(default=1, readonly=True, copy=False)
    parent_id = fields.Many2one("otm.gym.workout.plan", string="Previous version", readonly=True, copy=False)
    state = fields.Selection(PLAN_STATES, default="draft", required=True, index=True, tracking=True, copy=False)
    line_ids = fields.One2many("otm.gym.workout.plan.line", "plan_id", string="Exercises", copy=True)
    notes = fields.Text()
    line_count = fields.Integer(string="Exercise count", compute="_compute_line_count")

    @api.depends("line_ids")
    def _compute_line_count(self):
        for rec in self:
            rec.line_count = len(rec.line_ids)

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.end_date < rec.start_date:
                raise ValidationError(_("The end date cannot be before the start date."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.workout.plan") or "/"
            if not vals.get("trainer_id") and vals.get("member_id"):
                vals["trainer_id"] = self.env["otm.gym.member"].browse(vals["member_id"]).trainer_id.id
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.context.get("gym_state_write"):
            if set(vals) - {"notes"} and self.filtered(lambda r: r.state not in ("draft", "review")):
                raise UserError(_("An approved plan is part of the history. Use \"New version\" to change it."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("Only draft plans can be deleted; others are history."))
        return super().unlink()

    # ------------------------------------------------------------ actions
    def action_submit(self, reason=None, **kw):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Add at least one exercise before submitting for review."))
        return self._gym_move("action_submit", reason)

    def action_approve(self, reason=None, **kw):
        return self._gym_move("action_approve", reason)

    def action_reject(self, reason=None, **kw):
        return self._gym_move("action_reject", reason)

    def action_activate(self, reason=None, **kw):
        self._gym_move("action_activate", reason)
        for rec in self:
            others = self.search([("member_id", "=", rec.member_id.id), ("state", "=", "active"), ("id", "!=", rec.id)])
            for o in others:
                o._gym_log("auto_archived", "active", "archived", _("Replaced by %s", rec.name))
            others.with_context(gym_state_write=True).write({"state": "archived"})
            self.env["otm.gym.notification"]._gym_notify(
                "workout_updated", _("Workout updated: %s", rec.member_id.name),
                _("A new workout plan (v%(v)s) is now active.", v=rec.version), member=rec.member_id, rec=rec,
                users=rec.member_id.user_id | rec.member_id.trainer_id.user_id, key=f"workout:{rec.id}")
        return True

    def action_complete(self, reason=None, **kw):
        return self._gym_move("action_complete", reason)

    def action_archive(self, reason=None, **kw):
        return self._gym_move("action_archive", reason)

    def action_new_version(self, reason=None, **kw):
        self.ensure_one()
        self._gym_check_groups(["trainer", "manager"])
        top = self.search([("member_id", "=", self.member_id.id)], order="version desc", limit=1)
        new = self.with_context(gym_state_write=True).copy({"version": top.version + 1, "parent_id": self.id, "name": "/",
                                                            "state": "draft", "start_date": fields.Date.context_today(self)})
        new._gym_log("new_version", None, "draft", _("Copied from %s", self.name))
        return {"res_model": self._name, "res_id": new.id}


class GymWorkoutPlanLine(models.Model):
    _name = "otm.gym.workout.plan.line"
    _description = "Gym Workout Line"
    _order = "day, sequence, id"

    plan_id = fields.Many2one("otm.gym.workout.plan", required=True, ondelete="cascade", index=True)
    member_id = fields.Many2one(related="plan_id.member_id", store=True, index=True)
    day = fields.Selection(DAYS, required=True, default="monday")
    day_title = fields.Char(string="Day focus", help="e.g. Chest + Triceps")
    sequence = fields.Integer(default=10)
    exercise_id = fields.Many2one("otm.gym.exercise", string="Exercise", required=True)
    sets = fields.Integer(default=3)
    reps = fields.Char(default="10", help="10 or 8-12")
    weight = fields.Float(string="Weight (kg)")
    duration = fields.Float(string="Duration (min)")
    rest = fields.Integer(string="Rest (sec)", default=60)
    notes = fields.Char()

    @api.constrains("sets", "rest", "weight", "duration")
    def _check_values(self):
        for rec in self:
            if min(rec.sets, rec.rest, rec.weight, rec.duration) < 0:
                raise ValidationError(_("Sets, rest, weight and duration cannot be negative."))

    def _check_open(self):
        if not self.env.su and self.plan_id.filtered(lambda p: p.state not in ("draft", "review")):
            raise UserError(_("The plan is approved; create a new version to change exercises."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._check_open()
        return recs

    def write(self, vals):
        self._check_open()
        return super().write(vals)

    def unlink(self):
        self._check_open()
        return super().unlink()
