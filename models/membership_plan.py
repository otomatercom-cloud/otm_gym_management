from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class GymMembershipPlan(models.Model):
    _name = "otm.gym.membership.plan"
    _description = "Gym Membership Plan"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    plan_type = fields.Selection([
        ("trial", "Trial"), ("monthly", "Monthly"), ("quarterly", "Quarterly"), ("half_yearly", "Half Yearly"),
        ("yearly", "Yearly"), ("personal_training", "Personal Training"), ("class_package", "Class Package"),
        ("custom", "Custom")], default="monthly", required=True)
    duration = fields.Integer(default=1, required=True)
    duration_unit = fields.Selection([("days", "Days"), ("weeks", "Weeks"), ("months", "Months"), ("years", "Years")],
                                     default="months", required=True)
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    price = fields.Monetary(currency_field="currency_id")
    joining_fee = fields.Monetary(currency_field="currency_id")
    included_sessions = fields.Integer(string="Included sessions", help="Personal-training / class sessions included.")
    freeze_allowed = fields.Boolean(string="Freeze allowed")
    freeze_days = fields.Integer(string="Max freeze days")
    active = fields.Boolean(default=True)
    description = fields.Text()
    membership_count = fields.Integer(string="Memberships sold", compute="_compute_membership_count")

    _name_uniq = models.Constraint("unique(name)", "A membership plan with this name already exists.")

    @api.constrains("duration", "price", "joining_fee", "freeze_days")
    def _check_values(self):
        for rec in self:
            if rec.duration <= 0:
                raise ValidationError(_("Duration must be greater than zero."))
            if rec.price < 0 or rec.joining_fee < 0 or rec.freeze_days < 0:
                raise ValidationError(_("Amounts and freeze days cannot be negative."))

    def _compute_membership_count(self):
        data = {p.id: n for p, n in self.env["otm.gym.membership"]._read_group(
            [("plan_id", "in", self.ids)], ["plan_id"], ["__count"])}
        for rec in self:
            rec.membership_count = data.get(rec.id, 0)

    def _end_date_from(self, start):
        """Last valid day of a membership starting on ``start``."""
        self.ensure_one()
        if not start:
            return False
        unit = {"days": "days", "weeks": "weeks", "months": "months", "years": "years"}[self.duration_unit]
        return start + relativedelta(**{unit: self.duration}) - relativedelta(days=1)
