from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class GymHealthProfile(models.Model):
    """SENSITIVE: never exposed to Reception or Finance (ACL + record rules)."""
    _name = "otm.gym.health.profile"
    _description = "Gym Health Profile"
    _inherit = ["otm.gym.workflow.mixin"]
    _gym_sensitive = True
    _order = "member_id"
    _rec_name = "member_id"

    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="cascade")
    height = fields.Float(string="Height (cm)")
    weight = fields.Float(string="Weight (kg)")
    bmi = fields.Float(string="BMI", compute="_compute_bmi", store=True, digits=(4, 1))
    bmi_category = fields.Char(string="BMI category", compute="_compute_bmi", store=True)
    body_fat = fields.Float(string="Body fat %")
    muscle_mass = fields.Float(string="Muscle mass (kg)")
    blood_pressure = fields.Char(string="Blood pressure", help="e.g. 120/80")
    fitness_goal = fields.Selection(related="member_id.fitness_goal", string="Fitness goal")
    allergies = fields.Text()
    injuries = fields.Text()
    medical_restrictions = fields.Text()
    fitness_limitations = fields.Text()
    emergency_information = fields.Text()
    notes = fields.Text()

    _member_uniq = models.Constraint("unique(member_id)", "A member can have only one health profile.")

    @api.depends("height", "weight")
    def _compute_bmi(self):
        for rec in self:
            h = rec.height / 100.0
            rec.bmi = round(rec.weight / (h * h), 1) if h > 0 and rec.weight > 0 else 0.0
            b = rec.bmi
            rec.bmi_category = False if not b else ("Underweight" if b < 18.5 else "Normal" if b < 25 else "Overweight" if b < 30 else "Obese")

    @api.constrains("height", "weight", "body_fat", "muscle_mass")
    def _check_values(self):
        for rec in self:
            if min(rec.height, rec.weight, rec.body_fat, rec.muscle_mass) < 0 or rec.body_fat > 100:
                raise ValidationError(_("Health measurements must be positive (body fat 0-100%)."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        for rec in recs:
            rec._gym_log("health_created")
        return recs

    def write(self, vals):
        res = super().write(vals)
        changed = ", ".join(sorted(f for f in vals if f in self._fields))
        if changed:
            for rec in self:
                rec._gym_log("health_updated", reason=_("Fields: %s", changed))  # values are never copied into the log
        return res

    def unlink(self):
        for rec in self:
            rec._gym_log("health_deleted")
        return super().unlink()
