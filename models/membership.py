from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

STATES = [("draft", "Draft"), ("confirmed", "Confirmed"), ("active", "Active"), ("frozen", "Frozen"),
          ("expired", "Expired"), ("renewal_pending", "Renewal Pending"), ("renewed", "Renewed"),
          ("cancelled", "Cancelled")]
RENEWAL_STAGES = [("none", "-"), ("expiring", "Expiring"), ("pending", "Renewal Pending"),
                  ("contacted", "Customer Contacted"), ("payment_pending", "Payment Pending"), ("renewed", "Renewed")]
LIVE = ("active", "renewal_pending")


class GymMembership(models.Model):
    _name = "otm.gym.membership"
    _description = "Gym Membership"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, tracking=True, ondelete="restrict")
    plan_id = fields.Many2one("otm.gym.membership.plan", string="Plan", required=True, tracking=True,
                              domain="[('active','=',True)]")
    plan_type = fields.Selection(related="plan_id.plan_type", store=True)
    trainer_id = fields.Many2one("otm.gym.trainer", string="Trainer", tracking=True)
    start_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True, index=True)
    end_date = fields.Date(compute="_compute_end_date", store=True, readonly=False, index=True, tracking=True)
    freeze_extension_days = fields.Integer(string="Days added by freezes", readonly=True, copy=False)
    currency_id = fields.Many2one("res.currency", related="plan_id.currency_id", store=True)
    amount = fields.Monetary(compute="_compute_amount", store=True, readonly=False, currency_field="currency_id")
    discount = fields.Monetary(currency_field="currency_id", tracking=True)
    final_amount = fields.Monetary(compute="_compute_final", store=True, currency_field="currency_id")
    payment_ids = fields.One2many("otm.gym.payment", "membership_id", string="Payment charges")
    payment_status = fields.Selection([("pending", "Pending"), ("requested", "Requested"), ("partial", "Partially Paid"),
                                       ("paid", "Paid"), ("cancelled", "Cancelled")],
                                      compute="_compute_payment_status", store=True, index=True)
    state = fields.Selection(STATES, default="draft", required=True, index=True, tracking=True, copy=False)
    freeze_days_requested = fields.Integer(string="Freeze days to apply")
    freeze_start = fields.Date(readonly=True, copy=False)
    freeze_end = fields.Date(readonly=True, copy=False)
    freeze_reason = fields.Text(readonly=True, copy=False)
    freeze_days_used = fields.Integer(readonly=True, copy=False)
    renewal_date = fields.Date(compute="_compute_renewal_date", store=True)
    renewal_stage = fields.Selection(RENEWAL_STAGES, default="none", index=True, copy=False, readonly=True)
    renewal_of_id = fields.Many2one("otm.gym.membership", string="Renews", copy=False, readonly=True, index=True)
    renewed_by_ids = fields.One2many("otm.gym.membership", "renewal_of_id", string="Renewal memberships")
    included_sessions = fields.Integer(related="plan_id.included_sessions")
    days_left = fields.Integer(compute="_compute_days_left")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    # ------------------------------------------------------------ compute
    @api.depends("start_date", "plan_id", "freeze_extension_days")
    def _compute_end_date(self):
        for rec in self:
            end = rec.plan_id._end_date_from(rec.start_date) if rec.plan_id and rec.start_date else False
            rec.end_date = end and end + timedelta(days=rec.freeze_extension_days) or False

    @api.depends("plan_id", "member_id")
    def _compute_amount(self):
        for rec in self:
            plan = rec.plan_id
            first = not rec.member_id.membership_ids.filtered(lambda m: m.id != rec._origin.id and m.state not in ("draft", "cancelled"))
            rec.amount = (plan.price + (plan.joining_fee if first else 0.0)) if plan else 0.0

    @api.depends("amount", "discount")
    def _compute_final(self):
        for rec in self:
            rec.final_amount = max(rec.amount - rec.discount, 0.0)

    @api.depends("payment_ids.state", "payment_ids.paid_amount", "payment_ids.amount", "final_amount")
    def _compute_payment_status(self):
        for rec in self:
            live = rec.payment_ids.filtered(lambda p: p.state != "cancelled")
            if rec.final_amount <= 0:
                rec.payment_status = "paid"
            elif not rec.payment_ids:
                rec.payment_status = "pending"
            elif not live:
                rec.payment_status = "cancelled"
            else:
                paid = sum(live.mapped("paid_amount"))
                if paid >= rec.final_amount - 0.005:
                    rec.payment_status = "paid"
                elif paid > 0:
                    rec.payment_status = "partial"
                elif any(p.state == "requested" for p in live):
                    rec.payment_status = "requested"
                else:
                    rec.payment_status = "pending"

    @api.depends("end_date")
    def _compute_renewal_date(self):
        for rec in self:
            rec.renewal_date = rec.end_date and rec.end_date + timedelta(days=1) or False

    def _compute_days_left(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.days_left = (rec.end_date - today).days if rec.end_date and rec.state in LIVE + ("frozen",) else 0

    # -------------------------------------------------------- constraints
    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for rec in self:
            if rec.end_date and rec.start_date and rec.end_date < rec.start_date:
                raise ValidationError(_("The end date cannot be before the start date."))

    @api.constrains("member_id", "start_date", "end_date", "state")
    def _check_overlap(self):
        for rec in self.filtered(lambda r: r.state in ("confirmed", "active", "frozen", "renewal_pending")):
            clash = self.search([("id", "!=", rec.id), ("member_id", "=", rec.member_id.id),
                                 ("state", "in", ("confirmed", "active", "frozen", "renewal_pending")),
                                 ("start_date", "<=", rec.end_date), ("end_date", ">=", rec.start_date)], limit=1)
            if clash:
                raise ValidationError(_("%(m)s already has the membership %(c)s covering these dates.",
                                        m=rec.member_id.name, c=clash.name))

    @api.constrains("discount")
    def _check_discount(self):
        for rec in self:
            if rec.discount < 0 or rec.discount > rec.amount:
                raise ValidationError(_("The discount must be between 0 and the membership amount."))

    # --------------------------------------------------------------- CRUD
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.membership") or "/"
            if not vals.get("trainer_id") and vals.get("member_id"):
                vals["trainer_id"] = self.env["otm.gym.member"].browse(vals["member_id"]).trainer_id.id
        recs = super().create(vals_list)
        recs.mapped("member_id")._refresh_status()
        return recs

    def write(self, vals):
        locked = {"plan_id", "start_date", "amount", "discount", "member_id"}
        if locked & set(vals) and not self.env.su:
            if self.filtered(lambda r: r.state not in ("draft",)):
                raise UserError(_("Plan, dates and amounts can only be changed while the membership is in Draft."))
        res = super().write(vals)
        if "trainer_id" in vals:
            for rec in self:
                rec._gym_log("trainer_changed", None, rec.trainer_id.name)
        return res

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state != "draft"):
            raise UserError(_("Only draft memberships can be deleted. Cancel it instead."))
        return super().unlink()

    # ------------------------------------------------------------ actions
    def action_confirm(self, reason=None, **kw):
        for rec in self:
            if not rec.plan_id.active:
                raise UserError(_("The plan %s is archived.", rec.plan_id.name))
            if rec.member_id.status == "inactive":
                raise UserError(_("%s is inactive. Reactivate the member first.", rec.member_id.name))
            if rec.final_amount < 0:
                raise UserError(_("The amount cannot be negative."))
        self._gym_move("action_confirm", reason)
        for rec in self:
            rec._gym_make_charge()
            rec.member_id._refresh_status()
        return True

    def _gym_make_charge(self):
        self.ensure_one()
        if self.final_amount > 0 and not self.payment_ids.filtered(lambda p: p.state != "cancelled"):
            self.env["otm.gym.payment"].create({
                "member_id": self.member_id.id, "membership_id": self.id, "kind": "membership",
                "description": _("%(plan)s membership %(a)s - %(b)s", plan=self.plan_id.name,
                                 a=self.start_date, b=self.end_date),
                "amount": self.final_amount, "discount": self.discount, "due_date": self.start_date,
            })

    def action_activate(self, reason=None, **kw):
        for rec in self:
            if rec.payment_status != "paid" and rec.final_amount > 0:
                if not self._gym_has_group(["manager"]):
                    raise UserError(_("%s is not fully paid. Finance must confirm the payment first.", rec.name))
                if not (reason or "").strip():
                    raise UserError(_("Give a reason to activate a membership that is not fully paid."))
        self._gym_move("action_activate", reason)
        for rec in self:
            rec.member_id._refresh_status()
            if rec.renewal_of_id:
                rec.renewal_of_id._gym_renewed()
        return True

    def action_freeze(self, reason=None, **kw):
        for rec in self:
            plan = rec.plan_id
            days = rec.freeze_days_requested or plan.freeze_days
            if not plan.freeze_allowed:
                raise UserError(_("The plan %s does not allow freezing.", plan.name))
            if days <= 0 or rec.freeze_days_used + days > plan.freeze_days:
                raise UserError(_("Freeze days must be positive and within the plan allowance (%(left)s day(s) left).",
                                  left=max(plan.freeze_days - rec.freeze_days_used, 0)))
        today = fields.Date.context_today(self)
        for rec in self:
            days = rec.freeze_days_requested or rec.plan_id.freeze_days
            rec._gym_move("action_freeze", reason, vals={
                "freeze_start": today, "freeze_end": today + timedelta(days=days), "freeze_reason": reason})
        self.mapped("member_id")._refresh_status()
        return True

    def action_unfreeze(self, reason=None, **kw):
        today = fields.Date.context_today(self)
        for rec in self:
            planned = (rec.freeze_end - rec.freeze_start).days if rec.freeze_start and rec.freeze_end else 0
            used = max(min((today - rec.freeze_start).days, planned), 0) if rec.freeze_start else 0
            rec._gym_move("action_unfreeze", reason, vals={
                "freeze_days_used": rec.freeze_days_used + used,
                "freeze_extension_days": rec.freeze_extension_days + used,
                "freeze_start": False, "freeze_end": False})
        self.mapped("member_id")._refresh_status()
        return True

    def action_expire(self, reason=None, **kw):
        self._gym_move("action_expire", reason, vals={"renewal_stage": "pending"})
        self.mapped("member_id")._refresh_status()
        for rec in self:
            self.env["otm.gym.notification"]._gym_notify(
                "membership_expired", _("Membership expired: %s", rec.member_id.name),
                _("%(m)s's %(p)s membership ended on %(d)s.", m=rec.member_id.name, p=rec.plan_id.name, d=rec.end_date),
                member=rec.member_id, rec=rec, groups=["reception", "manager"], users=rec.member_id.user_id,
                key=f"expired:{rec.id}")
        return True

    def action_start_renewal(self, reason=None, **kw):
        return self._gym_move("action_start_renewal", reason, vals={"renewal_stage": "pending"})

    def action_mark_contacted(self, reason=None, **kw):
        return self._gym_move("action_mark_contacted", reason, vals={"renewal_stage": "contacted"})

    def action_renew(self, reason=None, **kw):
        self.ensure_one()
        self._gym_check_groups(["reception"])
        if not self.plan_id.active:
            raise UserError(_("The plan %s is archived. Create the new membership with another plan.", self.plan_id.name))
        open_new = self.renewed_by_ids.filtered(lambda m: m.state in ("draft", "confirmed"))
        if not open_new:
            today = fields.Date.context_today(self)
            start = max(self.end_date + timedelta(days=1), today) if self.state in LIVE else today
            new = self.create({"member_id": self.member_id.id, "plan_id": self.plan_id.id, "start_date": start,
                               "trainer_id": self.trainer_id.id, "renewal_of_id": self.id})
            new.action_confirm()
            open_new = new
        self._gym_move("action_renew", reason, vals={"renewal_stage": "payment_pending"})
        return {"res_model": "otm.gym.membership", "res_id": open_new[:1].id}

    def action_cancel(self, reason=None, **kw):
        for rec in self:
            if rec.state == "active":
                self._gym_check_groups(["manager"])
        self._gym_move("action_cancel", reason)
        for rec in self:
            rec.payment_ids.filtered(lambda p: p.state in ("pending", "requested"))._gym_system_cancel(reason)
        self.mapped("member_id")._refresh_status()
        return True

    def _gym_renewed(self):
        """Called when the renewal membership becomes active."""
        for rec in self:
            if rec.state in ("active", "expired", "renewal_pending"):
                rec.with_context(gym_state_write=True).write({"state": "renewed", "renewal_stage": "renewed"})
                rec._gym_log("auto_renewed", "renewal_pending", "renewed")
        self.mapped("member_id")._refresh_status()

    # --------------------------------------------------- auto activation
    def _gym_auto_activate(self):
        """Finance confirmed full payment of a confirmed membership: activate it (automatic action)."""
        for rec in self.filtered(lambda r: r.state == "confirmed" and r.payment_status == "paid"):
            rec.sudo().with_context(gym_auto=True).action_activate()
