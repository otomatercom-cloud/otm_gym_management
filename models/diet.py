from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .member import GOALS
from .workout import PLAN_STATES

MEALS = [("early_morning", "Early Morning"), ("breakfast", "Breakfast"), ("mid_morning", "Mid Morning"),
         ("lunch", "Lunch"), ("evening", "Evening"), ("pre_workout", "Pre Workout"),
         ("post_workout", "Post Workout"), ("dinner", "Dinner"), ("before_bed", "Before Bed")]


class GymDietPlan(models.Model):
    _name = "otm.gym.diet.plan"
    _description = "Gym Diet Plan"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _gym_sensitive = True
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, tracking=True, ondelete="restrict")
    nutritionist_id = fields.Many2one("res.users", string="Nutritionist", tracking=True, index=True, default=lambda s: s.env.user)
    trainer_id = fields.Many2one("otm.gym.trainer", string="Trainer", index=True)
    start_date = fields.Date(default=fields.Date.context_today)
    end_date = fields.Date()
    goal = fields.Selection(GOALS)
    calories_target = fields.Float(string="Calories target (kcal)")
    protein_target = fields.Float(string="Protein target (g)")
    carb_target = fields.Float(string="Carb target (g)")
    fat_target = fields.Float(string="Fat target (g)")
    water_target = fields.Float(string="Water target (L)")
    version = fields.Integer(default=1, readonly=True, copy=False)
    parent_id = fields.Many2one("otm.gym.diet.plan", string="Previous version", readonly=True, copy=False)
    state = fields.Selection(PLAN_STATES, default="draft", required=True, index=True, tracking=True, copy=False)
    line_ids = fields.One2many("otm.gym.diet.plan.line", "plan_id", string="Meals", copy=True)
    notes = fields.Text()
    calories_total = fields.Float(string="Calories (day)", compute="_compute_totals", store=True)
    protein_total = fields.Float(string="Protein (day, g)", compute="_compute_totals", store=True)
    carb_total = fields.Float(string="Carbs (day, g)", compute="_compute_totals", store=True)
    fat_total = fields.Float(string="Fat (day, g)", compute="_compute_totals", store=True)

    @api.depends("line_ids.calories", "line_ids.protein", "line_ids.carbs", "line_ids.fat")
    def _compute_totals(self):
        for rec in self:
            rec.calories_total = sum(rec.line_ids.mapped("calories"))
            rec.protein_total = sum(rec.line_ids.mapped("protein"))
            rec.carb_total = sum(rec.line_ids.mapped("carbs"))
            rec.fat_total = sum(rec.line_ids.mapped("fat"))

    @api.constrains("start_date", "end_date", "calories_target", "protein_target", "carb_target", "fat_target", "water_target")
    def _check_values(self):
        for rec in self:
            if rec.start_date and rec.end_date and rec.end_date < rec.start_date:
                raise ValidationError(_("The end date cannot be before the start date."))
            if min(rec.calories_target, rec.protein_target, rec.carb_target, rec.fat_target, rec.water_target) < 0:
                raise ValidationError(_("Targets cannot be negative."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.diet.plan") or "/"
            if not vals.get("trainer_id") and vals.get("member_id"):
                vals["trainer_id"] = self.env["otm.gym.member"].browse(vals["member_id"]).trainer_id.id
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.context.get("gym_state_write"):
            if set(vals) - {"notes"} and self.filtered(lambda r: r.state not in ("draft", "review")):
                raise UserError(_("An approved diet plan is part of the history. Use \"New version\" to change it."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("Only draft plans can be deleted; others are history."))
        return super().unlink()

    # ------------------------------------------------------------ actions
    def action_submit(self, reason=None, **kw):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Add at least one meal line before submitting for review."))
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
                "diet_updated", _("Diet updated: %s", rec.member_id.name),
                _("A new diet plan (v%(v)s) is now active.", v=rec.version), member=rec.member_id, rec=rec,
                users=rec.member_id.user_id | rec.nutritionist_id, key=f"diet:{rec.id}")
        return True

    def action_complete(self, reason=None, **kw):
        return self._gym_move("action_complete", reason)

    def action_archive(self, reason=None, **kw):
        return self._gym_move("action_archive", reason)

    def action_new_version(self, reason=None, **kw):
        self.ensure_one()
        self._gym_check_groups(["nutritionist", "manager"])
        top = self.search([("member_id", "=", self.member_id.id)], order="version desc", limit=1)
        new = self.with_context(gym_state_write=True).copy({"version": top.version + 1, "parent_id": self.id, "name": "/",
                                                            "state": "draft", "start_date": fields.Date.context_today(self)})
        new._gym_log("new_version", None, "draft", _("Copied from %s", self.name))
        return {"res_model": self._name, "res_id": new.id}


class GymDietPlanLine(models.Model):
    _name = "otm.gym.diet.plan.line"
    _description = "Gym Diet Meal Line"
    _order = "meal_type, sequence, id"

    plan_id = fields.Many2one("otm.gym.diet.plan", required=True, ondelete="cascade", index=True)
    member_id = fields.Many2one(related="plan_id.member_id", store=True, index=True)
    meal_type = fields.Selection(MEALS, required=True, default="breakfast")
    sequence = fields.Integer(default=10)
    food_id = fields.Many2one("otm.gym.food.item", string="Food", required=True)
    quantity = fields.Float(string="Servings", default=1.0)
    calories = fields.Float(compute="_compute_macros", store=True, string="Calories (kcal)")
    protein = fields.Float(compute="_compute_macros", store=True, string="Protein (g)")
    carbs = fields.Float(compute="_compute_macros", store=True, string="Carbs (g)")
    fat = fields.Float(compute="_compute_macros", store=True, string="Fat (g)")
    notes = fields.Char()

    @api.depends("food_id", "quantity")
    def _compute_macros(self):
        for rec in self:
            q = rec.quantity or 0.0
            rec.calories = rec.food_id.calories * q
            rec.protein = rec.food_id.protein * q
            rec.carbs = rec.food_id.carbs * q
            rec.fat = rec.food_id.fat * q

    @api.constrains("quantity")
    def _check_quantity(self):
        for rec in self:
            if rec.quantity <= 0:
                raise ValidationError(_("Servings must be greater than zero."))

    def _check_open(self):
        if not self.env.su and self.plan_id.filtered(lambda p: p.state not in ("draft", "review")):
            raise UserError(_("The plan is approved; create a new version to change meals."))

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
