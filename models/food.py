from odoo import fields, models, api, _
from odoo.exceptions import ValidationError

CATEGORIES = [("grains", "Grains & Cereals"), ("protein", "Protein"), ("dairy", "Dairy"), ("vegetables", "Vegetables"),
              ("fruits", "Fruits"), ("fats", "Nuts & Fats"), ("snacks", "Snacks"), ("beverages", "Beverages"),
              ("supplements", "Supplements"), ("other", "Other")]


class GymFoodItem(models.Model):
    _name = "otm.gym.food.item"
    _description = "Gym Food Item"
    _order = "category, name"

    name = fields.Char(required=True)
    category = fields.Selection(CATEGORIES, default="other", required=True, index=True)
    calories = fields.Float(string="Calories (kcal)", help="Per serving")
    protein = fields.Float(string="Protein (g)")
    carbs = fields.Float(string="Carbs (g)")
    fat = fields.Float(string="Fat (g)")
    serving_size = fields.Float(default=100.0)
    unit = fields.Selection([("g", "g"), ("ml", "ml"), ("piece", "piece"), ("cup", "cup"), ("tbsp", "tbsp"),
                             ("tsp", "tsp"), ("scoop", "scoop"), ("slice", "slice")], default="g", required=True)
    vegetarian = fields.Boolean(default=True)
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint("unique(name)", "This food item already exists.")

    @api.constrains("calories", "protein", "carbs", "fat", "serving_size")
    def _check_values(self):
        for rec in self:
            if min(rec.calories, rec.protein, rec.carbs, rec.fat) < 0 or rec.serving_size <= 0:
                raise ValidationError(_("Nutrition values cannot be negative and the serving size must be positive."))
