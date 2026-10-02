from odoo import api, fields, models, _
from odoo.exceptions import AccessError, UserError

from .transitions import GROUP_XMLID, TRANSITIONS

GROUP_LABEL = {
    "reception": "Reception", "trainer": "Trainer", "nutritionist": "Nutritionist",
    "assessor": "Assessment Staff", "finance": "Finance", "manager": "Gym Manager", "admin": "Gym Administrator",
}


class GymHistory(models.Model):
    """Audit trail (section 55): previous state, new state, action, user, date, reason."""
    _name = "otm.gym.history"
    _description = "Gym Audit Trail"
    _order = "id desc"
    _rec_name = "action"

    res_model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    record_name = fields.Char()
    member_id = fields.Many2one("otm.gym.member", string="Member", index=True, ondelete="set null")
    action = fields.Char(required=True)
    prev_state = fields.Char(string="Previous state")
    new_state = fields.Char(string="New state")
    user_id = fields.Many2one("res.users", string="By", default=lambda s: s.env.user, index=True)
    date = fields.Datetime(default=fields.Datetime.now, index=True)
    reason = fields.Text()
    sensitive = fields.Boolean(help="Health-related trail: hidden from Reception and Finance.")

    def write(self, vals):
        if not self.env.su:
            raise UserError(_("The audit trail cannot be edited."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("The audit trail cannot be deleted."))
        return super().unlink()


class GymWorkflowMixin(models.AbstractModel):
    """Controlled state machine: state fields can only change through Python action methods."""
    _name = "otm.gym.workflow.mixin"
    _description = "Gym controlled workflow"
    _gym_sensitive = False

    # ---------------------------------------------------------------- matrix
    def _gym_matrix(self):
        return TRANSITIONS.get(self._name, {})

    def _gym_member(self):
        """Member the audit row belongs to (override where the member is not member_id)."""
        return self.member_id if "member_id" in self._fields else self.env["otm.gym.member"]

    # ----------------------------------------------------------------- guards
    @api.model_create_multi
    def create(self, vals_list):
        m = self._gym_matrix()
        if m and not self.env.su and not self.env.context.get("gym_state_write"):
            for vals in vals_list:
                for f in m.get("guarded", []):
                    if f in vals and vals[f] != m.get("initial") and f == m["state_field"]:
                        raise UserError(_("The state of a new record is set by the workflow, not by hand."))
        return super().create(vals_list)

    def write(self, vals):
        m = self._gym_matrix()
        if m and not self.env.su and not self.env.context.get("gym_state_write"):
            bad = [f for f in m.get("guarded", []) if f in vals]
            if bad:
                raise UserError(_("The state cannot be changed directly. Use the workflow buttons "
                                  "(they check permissions and business rules)."))
        return super().write(vals)

    # --------------------------------------------------------------- helpers
    def _gym_has_group(self, names):
        if self.env.su:
            return True
        return any(self.env.user.has_group(GROUP_XMLID % n) for n in names)

    def _gym_check_groups(self, names, action=""):
        if not self._gym_has_group(names):
            raise AccessError(_("You are not allowed to do this. Required role: %s.",
                                " / ".join(GROUP_LABEL.get(n, n) for n in names)))

    def _gym_log(self, action, prev=None, new=None, reason=None):
        vals = []
        for rec in self:
            vals.append({
                "res_model": self._name, "res_id": rec.id, "record_name": rec.display_name,
                "member_id": rec._gym_member()[:1].id or False,
                "action": action, "prev_state": prev, "new_state": new, "reason": reason or False,
                "user_id": self.env.user.id, "sensitive": bool(self._gym_sensitive),
            })
        if vals:
            self.env["otm.gym.history"].sudo().create(vals)
        return True

    def _gym_move(self, action, reason=None, vals=None, to=None):
        """Validate role + state + reason, write the next state, audit it."""
        m = self._gym_matrix()
        spec = m["actions"][action]
        sf = m["state_field"]
        self._gym_check_groups(spec["groups"], action)
        reason = (reason or "").strip() or False
        if action in m.get("reason", []) and not reason:
            raise UserError(_("A reason is required for \"%s\".", action.replace("action_", "").replace("_", " ").title()))
        for rec in self:
            if rec[sf] not in spec["from"]:
                raise UserError(_("\"%(act)s\" is not allowed while the record is in state \"%(st)s\".",
                                  act=action.replace("action_", "").replace("_", " ").title(), st=rec[sf]))
        for rec in self:
            prev = rec[sf]
            new = to or spec["to"] or prev
            rec.with_context(gym_state_write=True).write(dict(vals or {}, **{sf: new}))
            rec._gym_log(action, prev, new, reason)
        return True

    @api.model
    def get_transition_matrix(self):
        return TRANSITIONS.get(self._name, {})
