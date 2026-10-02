from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

MEASURES = ["height", "weight", "body_fat", "muscle_mass", "chest", "waist", "hip", "arm", "thigh", "neck"]


class GymAssessment(models.Model):
    """Immutable fitness assessment history: a completed assessment is never edited, a new one is created."""
    _name = "otm.gym.assessment"
    _description = "Gym Fitness Assessment"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _gym_sensitive = True
    _order = "date desc, id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="restrict")
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    assessor_id = fields.Many2one("res.users", string="Assessor", default=lambda s: s.env.user)
    appointment_id = fields.Many2one("otm.gym.appointment", string="Appointment", copy=False)
    height = fields.Float(string="Height (cm)")
    weight = fields.Float(string="Weight (kg)")
    bmi = fields.Float(string="BMI", compute="_compute_bmi", store=True, digits=(4, 1))
    body_fat = fields.Float(string="Body fat %")
    muscle_mass = fields.Float(string="Muscle mass (kg)")
    chest = fields.Float(string="Chest (in)")
    waist = fields.Float(string="Waist (in)")
    hip = fields.Float(string="Hip (in)")
    arm = fields.Float(string="Arm (in)")
    thigh = fields.Float(string="Thigh (in)")
    neck = fields.Float(string="Neck (in)")
    line_ids = fields.One2many("otm.gym.assessment.line", "assessment_id", string="Fitness test results")
    notes = fields.Text()
    recommendations = fields.Text()
    state = fields.Selection([("draft", "Draft"), ("done", "Completed")], default="draft", required=True,
                             index=True, copy=False, tracking=True)

    @api.depends("height", "weight")
    def _compute_bmi(self):
        for rec in self:
            h = rec.height / 100.0
            rec.bmi = round(rec.weight / (h * h), 1) if h > 0 and rec.weight > 0 else 0.0

    @api.constrains(*MEASURES)
    def _check_values(self):
        for rec in self:
            if any(rec[f] < 0 for f in MEASURES) or rec.body_fat > 100:
                raise ValidationError(_("Measurements cannot be negative (body fat 0-100%)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.assessment") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and not self.env.context.get("gym_state_write") and self.filtered(lambda r: r.state == "done"):
            raise UserError(_("A completed assessment is part of the member's history and cannot be changed. "
                              "Create a new assessment instead."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state == "done"):
            raise UserError(_("A completed assessment cannot be deleted."))
        return super().unlink()

    def action_complete(self, reason=None, **kw):
        for rec in self:
            if not rec.weight or not rec.height:
                raise UserError(_("Enter at least height and weight before completing the assessment."))
        self._gym_move("action_complete", reason)
        for rec in self:
            member = rec.member_id.sudo()
            if not member.last_assessment_date or member.last_assessment_date < rec.date:
                member.write({"last_assessment_date": rec.date})
            # narrow side effect: keep the current vitals in the health profile up to date
            Health = self.env["otm.gym.health.profile"].sudo()
            vals = {"height": rec.height, "weight": rec.weight, "body_fat": rec.body_fat, "muscle_mass": rec.muscle_mass}
            prof = Health.search([("member_id", "=", member.id)], limit=1)
            if prof:
                prof.write(vals)
            else:
                Health.create(dict(vals, member_id=member.id))
        return True


class GymAssessmentLine(models.Model):
    _name = "otm.gym.assessment.line"
    _description = "Gym Assessment Test Result"
    _order = "sequence, id"

    assessment_id = fields.Many2one("otm.gym.assessment", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    test_name = fields.Char(string="Test", required=True, help="e.g. Push-ups (1 min), Plank, Sit & reach, 1.6 km run")
    result = fields.Float()
    unit = fields.Char()
    notes = fields.Char()

    def _check_open(self):
        if not self.env.su and self.assessment_id.filtered(lambda a: a.state == "done"):
            raise UserError(_("The assessment is completed; its results cannot be changed."))

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
