from odoo import fields, models

MUSCLE_GROUPS = [("chest", "Chest"), ("back", "Back"), ("shoulders", "Shoulders"), ("biceps", "Biceps"),
                 ("triceps", "Triceps"), ("forearms", "Forearms"), ("quads", "Quadriceps"), ("hamstrings", "Hamstrings"),
                 ("glutes", "Glutes"), ("calves", "Calves"), ("core", "Core / Abs"), ("cardio", "Cardio"),
                 ("full_body", "Full Body"), ("mobility", "Mobility / Stretch")]


class GymExercise(models.Model):
    _name = "otm.gym.exercise"
    _description = "Gym Exercise"
    _order = "muscle_group, name"

    name = fields.Char(required=True)
    muscle_group = fields.Selection(MUSCLE_GROUPS, required=True, index=True)
    equipment = fields.Char(help="Barbell, Dumbbell, Machine, Bodyweight, Cable...")
    difficulty = fields.Selection([("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")],
                                  default="beginner", required=True)
    instructions = fields.Html()
    image = fields.Image(max_width=1024, max_height=1024)
    video = fields.Char(string="Video link")
    safety_notes = fields.Text()
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint("unique(name)", "This exercise already exists.")
