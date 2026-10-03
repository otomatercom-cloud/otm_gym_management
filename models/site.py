from odoo import api, fields, models, _


class GymSite(models.Model):
    """Single record with the public front-page content of the Next.js app (logo, texts, gallery)."""
    _name = "otm.gym.site"
    _description = "Gym website content"

    name = fields.Char(string="Gym name", default="Gym Town", required=True)
    tagline = fields.Char(default="Train smarter. Live stronger.")
    logo = fields.Binary(attachment=True)
    hero_title = fields.Char(default="Train smarter. Run your gym in one place.")
    hero_subtitle = fields.Char(default="Members, attendance, workouts, diets and payments.")
    about_text = fields.Text()
    features = fields.Text(help="One feature per line, shown as a list on the login / front page.",
                           default="Members, memberships and renewals\nQR and RFID check-in\nWorkout, diet and health assessments\nPayments, receipts and reports")
    phone = fields.Char()
    email = fields.Char()
    address = fields.Text()
    opening_hours = fields.Char(default="Mon-Sat 05:30 - 22:00")
    image_ids = fields.One2many("otm.gym.site.image", "site_id", string="Front page pictures")

    @api.model
    def get_site(self):
        """The one content record (created on first use)."""
        return self.sudo().search([], limit=1) or self.sudo().create({})

    @api.model
    def public_payload(self):
        """Everything the public front page needs; no secrets, no member data."""
        s = self.get_site()
        ver = lambda r: str(r.write_date or "")[:19]  # noqa: E731 - cache buster
        return {
            "name": s.name, "tagline": s.tagline or "", "hero_title": s.hero_title or "", "hero_subtitle": s.hero_subtitle or "",
            "about_text": s.about_text or "", "features": [x.strip() for x in (s.features or "").splitlines() if x.strip()],
            "phone": s.phone or "", "email": s.email or "", "address": s.address or "", "opening_hours": s.opening_hours or "",
            "logo": ver(s) if s.logo else "",
            "images": [{"id": i.id, "caption": i.name or "", "v": ver(i)} for i in s.image_ids.filtered("active").sorted("sequence") if i.image],
        }

    @api.model
    def admin_state(self):
        """Id of the content record so the admin page can read/write it through normal RPC."""
        return self.get_site().id


class GymSiteImage(models.Model):
    _name = "otm.gym.site.image"
    _description = "Gym front page picture"
    _order = "sequence, id"

    site_id = fields.Many2one("otm.gym.site", required=True, ondelete="cascade", index=True)
    name = fields.Char(string="Caption")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    image = fields.Image(max_width=1920, max_height=1080, attachment=True)
