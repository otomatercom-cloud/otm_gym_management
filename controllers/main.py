from odoo import http
from odoo.http import request


class GymSiteController(http.Controller):
    """Public, read-only: front-page content and pictures for the login / landing page."""

    @http.route("/otm_gym/site", type="http", auth="public", methods=["GET"], csrf=False, readonly=True)
    def site(self, **kw):
        return request.make_json_response(request.env["otm.gym.site"].sudo().public_payload())

    @http.route("/otm_gym/site/logo", type="http", auth="public", methods=["GET"], csrf=False, readonly=True)
    def logo(self, **kw):
        site = request.env["otm.gym.site"].sudo().get_site()
        if not site.logo:
            return request.not_found()
        return request.env["ir.binary"]._get_image_stream_from(site, "logo", width=512).get_response(max_age=300)

    @http.route("/otm_gym/site/image/<int:image_id>", type="http", auth="public", methods=["GET"], csrf=False, readonly=True)
    def image(self, image_id, **kw):
        img = request.env["otm.gym.site.image"].sudo().browse(image_id).exists()
        if not img or not img.active or not img.image:
            return request.not_found()
        return request.env["ir.binary"]._get_image_stream_from(img, "image", width=1600).get_response(max_age=300)
