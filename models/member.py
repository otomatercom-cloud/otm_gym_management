import secrets

from odoo import api, fields, models, Command, _
from odoo.exceptions import UserError, ValidationError

GOALS = [("weight_loss", "Weight Loss"), ("muscle_gain", "Muscle Gain"), ("general_fitness", "General Fitness"),
         ("endurance", "Endurance"), ("flexibility", "Flexibility"), ("rehab", "Rehabilitation"),
         ("strength", "Strength"), ("sports", "Sports Performance")]


class GymMember(models.Model):
    _name = "otm.gym.member"
    _description = "Gym Member"
    _inherit = ["otm.gym.workflow.mixin", "image.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "member_code desc, id desc"
    _rec_names_search = ["name", "member_code", "phone", "whatsapp", "email"]

    member_code = fields.Char(string="Member ID", readonly=True, copy=False, index=True)
    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Contact", copy=False, index=True, ondelete="restrict")
    user_id = fields.Many2one("res.users", string="Portal login", copy=False, index=True,
                              help="Customer login used by the Gym app / portal.")
    date_of_birth = fields.Date()
    age = fields.Integer(compute="_compute_age")
    gender = fields.Selection([("male", "Male"), ("female", "Female"), ("other", "Other")])
    phone = fields.Char(tracking=True)
    whatsapp = fields.Char(string="WhatsApp")
    email = fields.Char()
    address = fields.Text()
    emergency_contact = fields.Char()
    emergency_phone = fields.Char()
    join_date = fields.Date(default=fields.Date.context_today, index=True)
    status = fields.Selection([("prospect", "Prospect"), ("active", "Active"), ("frozen", "Frozen"),
                               ("expired", "Expired"), ("inactive", "Inactive")],
                              default="prospect", required=True, index=True, tracking=True, copy=False)
    trainer_id = fields.Many2one("otm.gym.trainer", string="Assigned Trainer", index=True, tracking=True)
    nutritionist_id = fields.Many2one("res.users", string="Assigned Nutritionist", index=True, tracking=True)
    fitness_goal = fields.Selection(GOALS, tracking=True)
    notes = fields.Text()
    qr_token = fields.Char(string="QR token", copy=False, readonly=True, default=lambda s: secrets.token_urlsafe(16))
    rfid_code = fields.Char(string="RFID card", copy=False, index=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    # system-managed (never typed by users)
    current_membership_id = fields.Many2one("otm.gym.membership", string="Current Membership", readonly=True, copy=False)
    membership_expiry = fields.Date(related="current_membership_id.end_date", store=True, string="Membership Expiry")
    membership_state = fields.Selection(related="current_membership_id.state", store=True, string="Membership Status")
    plan_id = fields.Many2one(related="current_membership_id.plan_id", store=True, string="Plan")
    last_attendance_date = fields.Date(readonly=True, copy=False, index=True)
    last_assessment_date = fields.Date(readonly=True, copy=False, index=True)

    membership_ids = fields.One2many("otm.gym.membership", "member_id", string="Membership history")
    membership_count = fields.Integer(string="Memberships", compute="_compute_counts")
    attendance_count = fields.Integer(string="Visits", compute="_compute_counts")
    appointment_count = fields.Integer(string="Sessions", compute="_compute_counts")
    outstanding_amount = fields.Float(string="Outstanding", compute="_compute_counts")

    _code_uniq = models.Constraint("unique(member_code)", "Member ID must be unique.")

    # ------------------------------------------------------------ compute
    @api.depends("date_of_birth")
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for rec in self:
            d = rec.date_of_birth
            rec.age = (today.year - d.year - ((today.month, today.day) < (d.month, d.day))) if d else 0

    def _compute_counts(self):
        mem = dict(self.env["otm.gym.membership"]._read_group([("member_id", "in", self.ids)], ["member_id"], ["__count"]))
        att = dict(self.env["otm.gym.attendance"]._read_group(
            [("member_id", "in", self.ids), ("state", "in", ("present", "checked_out"))], ["member_id"], ["__count"]))
        apt = dict(self.env["otm.gym.appointment"]._read_group([("member_id", "in", self.ids)], ["member_id"], ["__count"]))
        due = dict(self.env["otm.gym.payment"]._read_group(
            [("member_id", "in", self.ids), ("state", "in", ("pending", "requested", "partial"))],
            ["member_id"], ["balance:sum"]))
        for rec in self:
            rec.membership_count = mem.get(rec, 0)
            rec.attendance_count = att.get(rec, 0)
            rec.appointment_count = apt.get(rec, 0)
            rec.outstanding_amount = due.get(rec, 0.0)

    # --------------------------------------------------------- CRUD / sync
    @api.model_create_multi
    def create(self, vals_list):
        seq = self.env["ir.sequence"]
        for vals in vals_list:
            vals.setdefault("member_code", seq.next_by_code("otm.gym.member") or "/")
            if not vals.get("partner_id"):
                vals["partner_id"] = self._gym_make_partner(vals).id
        recs = super().create(vals_list)
        for rec in recs:
            rec._gym_log("member_created", None, rec.status)
        return recs

    @api.model
    def _gym_make_partner(self, vals):
        # Reception has no partner-create right: the creation is a narrow, controlled side effect of the member.
        return self.env["res.partner"].sudo().create({
            "name": vals.get("name") or _("New member"), "phone": vals.get("phone") or False,
            "email": vals.get("email") or False, "street": vals.get("address") or False,
            "company_id": False,
        })

    def write(self, vals):
        if "trainer_id" in vals or "nutritionist_id" in vals:
            for rec in self:
                old = rec.trainer_id.name or "-"
                new = self.env["otm.gym.trainer"].browse(vals["trainer_id"]).name if vals.get("trainer_id") else "-"
                if "trainer_id" in vals and old != new:
                    rec._gym_log("trainer_changed", old, new)
        res = super().write(vals)
        if {"name", "phone", "email", "address"} & set(vals):
            for rec in self.filtered("partner_id"):
                rec.partner_id.sudo().write({k: rec[k] or False for k in ("name", "phone", "email")}
                                            | {"street": rec.address or False})
        return res

    @api.constrains("phone", "whatsapp", "email")
    def _check_contact(self):
        for rec in self:
            if rec.email and "@" not in rec.email:
                raise ValidationError(_("Please enter a valid e-mail address."))

    def unlink(self):
        if not self.env.su and self.filtered("membership_ids"):
            raise UserError(_("A member with membership history cannot be deleted. Deactivate the member instead."))
        return super().unlink()

    # ------------------------------------------------------------- status
    def _refresh_status(self):
        """Recompute status/current membership from memberships (never typed by hand)."""
        today = fields.Date.context_today(self)
        for rec in self:
            if rec.status == "inactive":
                continue
            ms = rec.membership_ids
            valid = ms.filtered(lambda m: m.state in ("active", "renewal_pending") and m.start_date and m.end_date
                                and m.start_date <= today <= m.end_date)
            frozen = ms.filtered(lambda m: m.state == "frozen")
            past = ms.filtered(lambda m: m.state in ("expired", "renewal_pending", "renewed"))
            if valid:
                status, cur = "active", valid.sorted("end_date")[-1]
            elif frozen:
                status, cur = "frozen", frozen[0]
            elif past:
                status, cur = "expired", past.sorted("end_date")[-1]
            else:
                status, cur = "prospect", ms.filtered(lambda m: m.state in ("draft", "confirmed"))[:1]
            vals = {"current_membership_id": cur.id or False}
            if status != rec.status:
                vals["status"] = status
                rec._gym_log("status_refresh", rec.status, status)
            rec.with_context(gym_state_write=True).write(vals)

    # ------------------------------------------------------------ actions
    def action_deactivate(self, reason=None, **kw):
        return self._gym_move("action_deactivate", reason)

    def action_reactivate(self, reason=None, **kw):
        self._gym_move("action_reactivate", reason)
        self._refresh_status()
        return True

    def action_create_portal_user(self, **kw):
        """Give the member a Gym app login (portal user with only the Gym Customer role)."""
        self._gym_check_groups(["reception"])
        Group = self.env.ref("otm_gym_management.group_gym_customer")
        for rec in self:
            if rec.user_id:
                continue
            login = (rec.email or "").strip().lower()
            if not login:
                raise UserError(_("Add the member's e-mail first - it is the login name."))
            if self.env["res.users"].sudo().with_context(active_test=False).search_count([("login", "=", login)]):
                raise UserError(_("A user with the login %s already exists.", login))
            user = self.env["res.users"].sudo().with_context(no_reset_password=True).create({
                "name": rec.name, "login": login, "email": login, "partner_id": rec.partner_id.id,
                "group_ids": [Command.set([Group.id])], "company_id": rec.company_id.id or self.env.company.id,
            })
            rec.sudo().write({"user_id": user.id})
            rec._gym_log("portal_user_created", None, login)
            try:
                with self.env.cr.savepoint():
                    user.action_reset_password()
            except Exception:  # mail server may be missing; the account is still created
                pass
        return True

    def action_open_check_in(self, **kw):
        self.ensure_one()
        return self.env["otm.gym.attendance"].check_in_member(self.id, "reception")

    def _gym_member(self):
        return self

    # ---------------------------------------------------- RPC read helpers
    def get_card(self):
        """Compact header used by lists/360."""
        self.ensure_one()
        return {"id": self.id, "name": self.name, "code": self.member_code, "status": self.status,
                "expiry": self.membership_expiry and str(self.membership_expiry) or False,
                "trainer": self.trainer_id.name or False, "goal": self.fitness_goal and dict(GOALS).get(self.fitness_goal) or False}
