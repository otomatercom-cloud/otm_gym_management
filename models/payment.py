import logging

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)
METHODS = [("cash", "Cash"), ("upi", "UPI"), ("card", "Card"), ("bank", "Bank Transfer"), ("cheque", "Cheque"),
           ("other", "Other")]


class GymPayment(models.Model):
    """A charge raised for a member (membership, PT, package). Money received is recorded as immutable receipts
    confirmed by Finance and traced to Odoo Accounting (account.move / account.payment)."""
    _name = "otm.gym.payment"
    _description = "Gym Payment Charge"
    _inherit = ["otm.gym.workflow.mixin", "mail.thread"]
    _order = "id desc"

    name = fields.Char(string="Reference", readonly=True, copy=False, default="/")
    member_id = fields.Many2one("otm.gym.member", string="Member", required=True, index=True, ondelete="restrict", tracking=True)
    membership_id = fields.Many2one("otm.gym.membership", string="Membership", index=True, ondelete="set null")
    kind = fields.Selection([("membership", "Membership"), ("personal_training", "Personal Training"),
                             ("package", "Package"), ("other", "Other")], default="membership", required=True, index=True)
    description = fields.Char()
    currency_id = fields.Many2one("res.currency", default=lambda s: s.env.company.currency_id)
    amount = fields.Monetary(string="Amount due", required=True, currency_field="currency_id", tracking=True)
    discount = fields.Monetary(string="Discount given", currency_field="currency_id")
    paid_amount = fields.Monetary(string="Paid", compute="_compute_paid", store=True, currency_field="currency_id")
    balance = fields.Monetary(string="Outstanding", compute="_compute_paid", store=True, currency_field="currency_id")
    refund_amount = fields.Monetary(string="Refunded", compute="_compute_paid", store=True, currency_field="currency_id")
    due_date = fields.Date(index=True)
    state = fields.Selection([("pending", "Pending"), ("requested", "Requested"), ("partial", "Partially Paid"),
                              ("paid", "Paid"), ("cancelled", "Cancelled")], default="pending", required=True,
                             index=True, tracking=True, copy=False)
    receipt_ids = fields.One2many("otm.gym.payment.receipt", "payment_id", string="Receipts")
    # inputs Finance fills before pressing "Confirm payment"
    receive_amount = fields.Monetary(string="Amount received now", currency_field="currency_id", copy=False)
    receive_method = fields.Selection(METHODS, string="Method", copy=False)
    receive_reference = fields.Char(string="Reference / transaction id", copy=False)
    receive_date = fields.Date(string="Received on", copy=False)
    refund_amount_input = fields.Monetary(string="Amount to refund", currency_field="currency_id", copy=False)
    move_id = fields.Many2one("account.move", string="Invoice", readonly=True, copy=False)
    accounting_note = fields.Char(readonly=True, copy=False)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("receipt_ids.amount", "amount")
    def _compute_paid(self):
        for rec in self:
            paid = sum(rec.receipt_ids.filtered(lambda r: r.kind == "payment").mapped("amount"))
            refund = -sum(rec.receipt_ids.filtered(lambda r: r.kind == "refund").mapped("amount"))
            rec.paid_amount = paid - refund
            rec.refund_amount = refund
            rec.balance = max(rec.amount - (paid - refund), 0.0)

    @api.constrains("amount", "discount")
    def _check_amounts(self):
        for rec in self:
            if rec.amount < 0 or rec.discount < 0:
                raise ValidationError(_("Amounts cannot be negative."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "/") == "/":
                vals["name"] = self.env["ir.sequence"].next_by_code("otm.gym.payment") or "/"
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and ({"amount", "member_id", "membership_id", "kind", "discount"} & set(vals)):
            if self.filtered(lambda r: r.state != "pending"):
                raise UserError(_("The amount of a requested or paid charge cannot be changed."))
        res = super().write(vals)
        if {"amount", "discount"} & set(vals):
            for rec in self:
                rec._gym_log("amount_changed", None, None, _("Amount %s", rec.amount))
        return res

    def unlink(self):
        if not self.env.su and self.filtered(lambda r: r.state != "pending" or r.receipt_ids):
            raise UserError(_("Only untouched pending charges can be deleted. Cancel it instead."))
        return super().unlink()

    def _state_from_amounts(self):
        self.ensure_one()
        if self.paid_amount >= self.amount - 0.005 and self.amount > 0:
            return "paid"
        if self.paid_amount > 0:
            return "partial"
        return "requested" if self.move_id else "pending"

    # ------------------------------------------------------------ actions
    def action_request(self, reason=None, **kw):
        for rec in self:
            if rec.amount <= 0:
                raise UserError(_("There is nothing to request: the amount is zero."))
        self._gym_move("action_request", reason)
        for rec in self:
            rec._gym_make_invoice()
            self.env["otm.gym.notification"]._gym_notify(
                "payment_due", _("Payment requested: %s", rec.member_id.name),
                _("%(n)s - %(a)s due.", n=rec.name, a=rec.amount), member=rec.member_id, rec=rec,
                groups=["finance"], users=rec.member_id.user_id, key=f"requested:{rec.id}")
        return True

    def _gym_make_invoice(self):
        """Customer invoice in Odoo Accounting. Gym users are not accountants, so this controlled side effect is sudo'd."""
        self.ensure_one()
        if self.move_id:
            return
        try:
            with self.env.cr.savepoint():
                move = self.env["account.move"].sudo().with_company(self.company_id).create({
                    "move_type": "out_invoice", "partner_id": self.member_id.partner_id.id,
                    "invoice_date": fields.Date.context_today(self), "ref": self.name, "invoice_origin": self.name,
                    "invoice_line_ids": [Command.create({
                        "name": self.description or self.name, "quantity": 1.0, "price_unit": self.amount})],
                })
                move.action_post()
            self.with_context(gym_state_write=True).write({"move_id": move.id, "accounting_note": False})
        except Exception as e:  # accounting not configured (no chart/journal) must not block the gym workflow
            _logger.warning("Gym invoice for %s not created: %s", self.name, e)
            self.with_context(gym_state_write=True).write({"accounting_note": _("Invoice not created: %s", str(e)[:200])})

    def action_confirm_payment(self, reason=None, **kw):
        for rec in self:
            amount = rec.receive_amount or rec.balance
            if amount <= 0:
                raise UserError(_("Enter the amount received."))
            if amount > rec.balance + 0.005:
                raise UserError(_("%(a)s is more than the outstanding balance (%(b)s).", a=amount, b=rec.balance))
            if not rec.receive_method:
                raise UserError(_("Choose how the payment was received (cash, UPI, card...)."))
        for rec in self:
            amount = rec.receive_amount or rec.balance
            receipt = self.env["otm.gym.payment.receipt"].create({
                "payment_id": rec.id, "kind": "payment", "amount": amount, "method": rec.receive_method,
                "reference": rec.receive_reference, "date": rec.receive_date or fields.Date.context_today(self),
                "confirmed_by_id": self.env.user.id})
            rec.invalidate_recordset(["paid_amount", "balance", "refund_amount"])
            rec._gym_move("action_confirm_payment", reason, to=rec._state_from_amounts(), vals={
                "receive_amount": 0, "receive_method": False, "receive_reference": False, "receive_date": False})
            receipt._gym_register_account_payment()
            rec.membership_id._compute_payment_status()
            rec.membership_id._gym_auto_activate()
            self.env["otm.gym.notification"]._gym_notify(
                "payment_received", _("Payment received: %s", rec.member_id.name),
                _("%(a)s received for %(n)s.", a=amount, n=rec.name), member=rec.member_id, rec=rec,
                groups=["manager"], users=rec.member_id.user_id, key=f"receipt:{receipt.id}")
        return True

    def action_refund(self, reason=None, **kw):
        for rec in self:
            amount = rec.refund_amount_input or rec.paid_amount
            if amount <= 0 or amount > rec.paid_amount + 0.005:
                raise UserError(_("The refund must be between 0 and the amount paid (%s).", rec.paid_amount))
        for rec in self:
            amount = rec.refund_amount_input or rec.paid_amount
            self.env["otm.gym.payment.receipt"].create({
                "payment_id": rec.id, "kind": "refund", "amount": -amount, "method": "other",
                "reference": reason, "date": fields.Date.context_today(self), "confirmed_by_id": self.env.user.id,
                "note": _("Refund - credit note to be issued in Accounting")})
            rec.invalidate_recordset(["paid_amount", "balance", "refund_amount"])
            rec._gym_move("action_refund", reason, to=rec._state_from_amounts(), vals={"refund_amount_input": 0})
            rec.membership_id._compute_payment_status()
        return True

    def action_cancel(self, reason=None, **kw):
        for rec in self:
            if rec.receipt_ids:
                raise UserError(_("Money was already received for %s; refund it instead of cancelling.", rec.name))
        return self._gym_move("action_cancel", reason)

    def _gym_system_cancel(self, reason=None):
        """Automatic cancel when the membership is cancelled (no receipts allowed to exist)."""
        for rec in self.filtered(lambda p: not p.receipt_ids):
            rec.sudo()._gym_move("action_cancel", reason or _("Membership cancelled"))


class GymPaymentReceipt(models.Model):
    _name = "otm.gym.payment.receipt"
    _description = "Gym Payment Receipt (immutable)"
    _order = "id desc"

    payment_id = fields.Many2one("otm.gym.payment", required=True, ondelete="restrict", index=True)
    member_id = fields.Many2one(related="payment_id.member_id", store=True, index=True)
    kind = fields.Selection([("payment", "Payment"), ("refund", "Refund")], default="payment", required=True)
    currency_id = fields.Many2one(related="payment_id.currency_id")
    amount = fields.Monetary(currency_field="currency_id")
    date = fields.Date(default=fields.Date.context_today, index=True)
    method = fields.Selection(METHODS)
    reference = fields.Char()
    note = fields.Char()
    confirmed_by_id = fields.Many2one("res.users", string="Confirmed by", readonly=True)
    account_payment_id = fields.Many2one("account.payment", string="Accounting payment", readonly=True)

    def write(self, vals):
        if not self.env.su and set(vals) - {"account_payment_id"}:
            raise UserError(_("Receipts are immutable. Record a refund instead."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_("Receipts cannot be deleted."))
        return super().unlink()

    def _gym_register_account_payment(self):
        """Register the money in Accounting against the invoice (best effort, never blocks Finance)."""
        for rec in self.filtered(lambda r: r.kind == "payment"):
            move = rec.payment_id.move_id.sudo()
            if not move or move.state != "posted" or move.payment_state in ("paid", "in_payment"):
                continue
            try:
                with self.env.cr.savepoint():
                    wiz = self.env["account.payment.register"].sudo().with_context(
                        active_model="account.move", active_ids=move.ids).create({
                            "amount": rec.amount, "payment_date": rec.date, "communication": rec.payment_id.name})
                    payments = wiz._create_payments()
                rec.sudo().write({"account_payment_id": payments[:1].id})
            except Exception as e:
                _logger.warning("Gym receipt %s not registered in Accounting: %s", rec.id, e)
                rec.payment_id.sudo().with_context(gym_state_write=True).write(
                    {"accounting_note": _("Payment not registered in Accounting: %s", str(e)[:180])})
