from odoo import api, fields, models, _

TYPES = [("membership_expiring", "Membership expiring"), ("membership_expired", "Membership expired"),
         ("payment_due", "Payment due"), ("payment_overdue", "Payment overdue"), ("payment_received", "Payment received"),
         ("appointment_reminder", "Appointment reminder"), ("assessment_due", "Assessment due"),
         ("workout_updated", "Workout updated"), ("diet_updated", "Diet updated"),
         ("trainer_session", "Trainer session"), ("renewal_reminder", "Renewal reminder"),
         ("long_absence", "Long absence")]
STAFF_ROLES = ("reception", "trainer", "nutritionist", "assessor", "finance", "manager")


class GymNotification(models.Model):
    """Role-aware in-app notifications. `dedupe_key` guarantees a reminder is created only once."""
    _name = "otm.gym.notification"
    _description = "Gym Notification"
    _order = "id desc"
    _rec_name = "title"

    ntype = fields.Selection(TYPES, string="Type", required=True, index=True)
    title = fields.Char(required=True)
    body = fields.Char()
    member_id = fields.Many2one("otm.gym.member", index=True, ondelete="cascade")
    res_model = fields.Char()
    res_id = fields.Integer()
    recipient_groups = fields.Char(help="Comma separated gym roles that receive it (reception, finance, manager...).")
    user_ids = fields.Many2many("res.users", "otm_gym_notification_user_rel", "notification_id", "user_id", string="Recipients")
    dedupe_key = fields.Char(index=True, copy=False)
    date = fields.Datetime(default=fields.Datetime.now, index=True)

    _dedupe_uniq = models.Constraint("unique(dedupe_key)", "This notification was already created.")

    @api.model
    def _gym_notify(self, ntype, title, body="", member=None, rec=None, groups=(), users=None, key=None):
        """Create a notification once (same ``key`` never creates twice)."""
        Notif = self.sudo()
        if key:
            if Notif.search_count([("dedupe_key", "=", key)]):
                return Notif.browse()
        users = users if users is not None else self.env["res.users"]
        return Notif.create({
            "ntype": ntype, "title": title, "body": body, "member_id": member.id if member else False,
            "res_model": rec._name if rec else False, "res_id": rec.id if rec else False,
            "recipient_groups": ",".join(groups) or False, "user_ids": [(6, 0, users.filtered("id").ids)],
            "dedupe_key": key or False,
        })

    @api.model
    def _gym_domain_for(self, user):
        """Notifications this user may see: addressed to them, or to one of their gym roles."""
        dom = [("user_ids", "in", user.id)]
        for role in STAFF_ROLES:
            if user.has_group("otm_gym_management.group_gym_%s" % role):
                dom = ["|"] + dom + [("recipient_groups", "ilike", role)]
        return dom
