from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

SERIES = [("weight", "Weight (kg)"), ("bmi", "BMI"), ("body_fat", "Body Fat %"), ("muscle_mass", "Muscle Mass (kg)"),
          ("chest", "Chest"), ("waist", "Waist"), ("hip", "Hip"), ("arm", "Arm"), ("thigh", "Thigh")]


class GymProgress(models.Model):
    """Measurement log between assessments (member / trainer). Append-only: history is never overwritten."""
    _name = "otm.gym.progress"
    _description = "Gym Progress Log"
    _inherit = ["otm.gym.workflow.mixin"]
    _gym_sensitive = True
    _order = "date desc, id desc"

    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="cascade")
    date = fields.Date(required=True, default=fields.Date.context_today, index=True)
    source = fields.Selection([("self", "Member"), ("trainer", "Trainer")], default="trainer")
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
    notes = fields.Char()

    @api.depends("height", "weight")
    def _compute_bmi(self):
        for rec in self:
            h = rec.height / 100.0
            rec.bmi = round(rec.weight / (h * h), 1) if h > 0 and rec.weight > 0 else 0.0

    @api.constrains("weight", "body_fat", "muscle_mass", "chest", "waist", "hip", "arm", "thigh")
    def _check_values(self):
        for rec in self:
            if min(rec.weight, rec.body_fat, rec.muscle_mass, rec.chest, rec.waist, rec.hip, rec.arm, rec.thigh) < 0:
                raise ValidationError(_("Measurements cannot be negative."))
