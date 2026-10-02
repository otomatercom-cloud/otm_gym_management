from odoo import fields, models, _
from odoo.exceptions import UserError


class GymReasonWizard(models.TransientModel):
    """Asks for the mandatory reason of a workflow action (cancel, freeze, reject...) in the Odoo back office."""
    _name = "otm.gym.reason.wizard"
    _description = "Gym workflow reason"

    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    method = fields.Char(required=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        if not self.method.startswith("action_"):
            raise UserError(_("Invalid action."))
        rec = self.env[self.res_model].browse(self.res_id).exists()
        if not rec:
            raise UserError(_("The record no longer exists."))
        res = getattr(rec, self.method)(reason=self.reason)
        if isinstance(res, dict) and res.get("res_model") and res.get("res_id"):
            return {"type": "ir.actions.act_window", "res_model": res["res_model"], "res_id": res["res_id"],
                    "view_mode": "form", "target": "current"}
        return {"type": "ir.actions.act_window_close"}
