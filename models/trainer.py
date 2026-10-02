from odoo import api, fields, models, _


class GymTrainer(models.Model):
    _name = "otm.gym.trainer"
    _description = "Gym Trainer / Staff"
    _inherit = ["image.mixin", "mail.thread"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    employee_id = fields.Many2one("hr.employee", string="Employee", tracking=True)
    user_id = fields.Many2one("res.users", string="Login user", tracking=True,
                              help="The Odoo user of this trainer. Used by record rules (a trainer only sees assigned members).")
    staff_role = fields.Selection([("trainer", "Trainer"), ("nutritionist", "Nutritionist"),
                                   ("assessor", "Assessment Staff")], default="trainer", required=True)
    specialization = fields.Char()
    certification = fields.Char()
    experience = fields.Float(string="Experience (years)")
    phone = fields.Char()
    email = fields.Char()
    working_hours = fields.Char(help="e.g. 06:00 - 14:00 (Mon-Sat)")
    status = fields.Selection([("active", "Active"), ("on_leave", "On Leave"), ("inactive", "Inactive")],
                              default="active", required=True, tracking=True)
    member_ids = fields.One2many("otm.gym.member", "trainer_id", string="Assigned Members")
    member_count = fields.Integer(string="Members assigned", compute="_compute_member_count")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("member_ids")
    def _compute_member_count(self):
        data = {t.id: n for t, n in self.env["otm.gym.member"]._read_group(
            [("trainer_id", "in", self.ids)], ["trainer_id"], ["__count"])}
        for rec in self:
            rec.member_count = data.get(rec.id, 0)

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id:
            self.name = self.name or self.employee_id.name
            self.user_id = self.user_id or self.employee_id.user_id
