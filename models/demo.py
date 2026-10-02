import logging
import random
from datetime import datetime, time, timedelta

from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)
PASSWORD = "Demo@1234"
STAFF = [  # login, name, group, trainer-record role (or None)
    ("demo.manager", "Demo Manager", "manager", None),
    ("demo.reception", "Demo Reception", "reception", None),
    ("demo.finance", "Demo Finance", "finance", None),
    ("demo.trainer1", "Arjun Menon (Trainer)", "trainer", "trainer"),
    ("demo.trainer2", "Neha Varma (Trainer)", "trainer", "trainer"),
    ("demo.nutritionist", "Dr. Anjali Nair (Nutritionist)", "nutritionist", "nutritionist"),
    ("demo.assessor", "Rahul Das (Assessment)", "assessor", "assessor"),
]
MEMBERS = [  # name, gender, goal, trainer idx, plan offset, start days ago, paid (full/partial/none), age
    ("Aarav Sharma", "male", "muscle_gain", 0, 0, 20, "full", 27), ("Diya Nair", "female", "weight_loss", 1, 1, 12, "full", 31),
    ("Rohan Pillai", "male", "strength", 0, 0, 25, "full", 35), ("Meera Krishnan", "female", "general_fitness", 1, 2, 5, "partial", 29),
    ("Vikram Iyer", "male", "endurance", 0, 1, 18, "full", 41), ("Sneha George", "female", "flexibility", 1, 0, 2, "none", 24),
    ("Karthik Raj", "male", "weight_loss", 0, 2, 70, "full", 38), ("Anu Mathew", "female", "rehab", 1, 1, 80, "full", 45),
    ("Faisal Ahmed", "male", "sports", 0, 0, 10, "full", 22), ("Lakshmi Menon", "female", "weight_loss", 1, 2, 15, "full", 33),
    ("Joseph Thomas", "male", "muscle_gain", 0, 1, 27, "full", 30), ("Priya Suresh", "female", "general_fitness", 1, 0, 8, "full", 26),
]
METHODS = ["cash", "upi", "card", "bank"]


class GymDemo(models.AbstractModel):
    _name = "otm.gym.demo"
    _description = "Gym demo data loader"

    @api.model
    def load_demo(self):
        """Idempotent demo data: staff logins, trainers, members, memberships, payments, attendance, plans, sessions."""
        if not (self.env.su or self.env.user.has_group("otm_gym_management.group_gym_admin")
                or self.env.user.has_group("otm_gym_management.group_gym_manager")):
            raise AccessError(_("Only a Gym Manager or Admin can load demo data."))
        env = self.sudo().env
        rnd = random.Random(7)
        log = []

        def step(name, fn):
            try:
                with env.cr.savepoint():
                    fn()
                log.append("OK " + name)
            except Exception as e:  # noqa: BLE001 - report, never block the other sections
                _logger.exception("Gym demo step failed: %s", name)
                log.append("FAILED %s: %s" % (name, e))

        ctx = {"no_reset_password": True, "mail_notrack": True}
        Users, Trainer, Member = env["res.users"].with_context(**ctx), env["otm.gym.trainer"], env["otm.gym.member"]
        users, trainers = {}, []

        def make_staff():
            for login, name, grp, role in STAFF:
                u = Users.search([("login", "=", login)], limit=1) or Users.create({
                    "name": name, "login": login, "email": login + "@example.com", "password": PASSWORD,
                    "group_ids": [Command.set([env.ref("otm_gym_management.group_gym_" + grp).id])]})
                users[login] = u
                if role:
                    t = Trainer.search([("user_id", "=", u.id)], limit=1) or Trainer.create({
                        "name": name.split(" (")[0], "user_id": u.id, "staff_role": role,
                        "specialization": {"trainer": "Strength and conditioning", "nutritionist": "Clinical nutrition",
                                           "assessor": "Fitness testing"}[role],
                        "certification": "ACE / ISSA certified", "experience": rnd.randint(3, 9), "phone": "90000%05d" % rnd.randint(0, 99999),
                        "email": login + "@example.com", "working_hours": "06:00 - 14:00 (Mon-Sat)"})
                    if role == "trainer":
                        trainers.append(t)
        step("staff logins and trainers", make_staff)
        if not trainers:
            return self._done(log)

        plans = env["otm.gym.membership.plan"].search([], order="sequence, id")
        exercises, foods = env["otm.gym.exercise"].search([], limit=15), env["otm.gym.food.item"].search([], limit=15)
        if not (plans and exercises and foods):
            log.append("FAILED seed data missing (plans/exercises/foods)")
            return self._done(log)
        # shortest plan first so 'expired' demos really are expired
        plans = plans.sorted(key=lambda p: p.duration * {"days": 1, "weeks": 7, "months": 30, "years": 365}.get(p.duration_unit, 30))
        today = fields.Date.context_today(self)
        members = []

        def make_members():
            for i, (name, gender, goal, ti, po, ago, paid, age) in enumerate(MEMBERS):
                m = Member.search([("name", "=", name), ("phone", "=", "98470%05d" % (1000 + i))], limit=1)
                if m:
                    members.append((m, paid, po, ago))
                    continue
                m = Member.create({
                    "name": name, "gender": gender, "fitness_goal": goal, "phone": "98470%05d" % (1000 + i),
                    "whatsapp": "98470%05d" % (1000 + i), "email": name.lower().replace(" ", ".") + "@example.com",
                    "date_of_birth": today - timedelta(days=age * 365 + 40), "trainer_id": trainers[ti % len(trainers)].id,
                    "nutritionist_id": users["demo.nutritionist"].id, "join_date": today - timedelta(days=ago),
                    "emergency_contact": "Family of " + name.split()[0], "emergency_phone": "9000000000",
                    "address": "Kochi, Kerala", "rfid_code": "RFID%04d" % (1000 + i)})
                members.append((m, paid, po, ago))
        step("members", make_members)

        def make_memberships():
            for m, paid, po, ago in members:
                if m.membership_ids:
                    continue
                plan = plans[po % len(plans)] if ago < 60 else plans[0]
                ms = env["otm.gym.membership"].create({"member_id": m.id, "plan_id": plan.id, "start_date": today - timedelta(days=ago)})
                ms.action_confirm()
                pay = env["otm.gym.payment"].search([("membership_id", "=", ms.id)], limit=1)
                if pay and paid != "none":
                    pay.action_request()
                    pay.write({"receive_amount": pay.amount if paid == "full" else round(pay.amount / 2, 2),
                               "receive_method": rnd.choice(METHODS), "receive_reference": "DEMO%05d" % rnd.randint(0, 99999),
                               "receive_date": today - timedelta(days=max(ago - 1, 0))})
                    pay.action_confirm_payment()
                if paid == "full" and ms.state == "confirmed":
                    ms.action_activate(reason="Demo data")
                if ms.state == "active" and ms.end_date and ms.end_date < today:
                    ms.action_expire(reason="Demo data")
        step("memberships and payments", make_memberships)

        def make_portal():
            m = members[0][0]
            if not m.user_id:
                u = Users.search([("login", "=", "demo.member")], limit=1) or Users.create({
                    "name": m.name, "login": "demo.member", "email": "demo.member@example.com", "password": PASSWORD,
                    "partner_id": m.partner_id.id, "group_ids": [Command.set([env.ref("otm_gym_management.group_gym_customer").id])]})
                m.user_id = u
        step("customer portal login", make_portal)

        def make_attendance():
            Att = env["otm.gym.attendance"].with_context(gym_state_write=True)
            for m, paid, po, ago in members:
                if paid == "none" or Att.search_count([("member_id", "=", m.id)]):
                    continue
                ms = m.current_membership_id
                for d in range(1, min(ago, 14) + 1):
                    if rnd.random() < 0.6:
                        day = today - timedelta(days=d)
                        if ms and (day < ms.start_date or day > ms.end_date):
                            continue
                        ci = datetime.combine(day, time(rnd.randint(5, 19), rnd.choice([0, 15, 30, 45])))
                        Att.create({"member_id": m.id, "check_in": ci, "check_out": ci + timedelta(minutes=rnd.randint(45, 100)),
                                    "state": "checked_out", "source": rnd.choice(["reception", "qr", "rfid"]),
                                    "trainer_id": m.trainer_id.id, "membership_id": ms.id if ms else False})
            # three members inside the gym right now
            for m, paid, po, ago in members[:3]:
                if m.status == "active":
                    try:
                        with env.cr.savepoint():
                            env["otm.gym.attendance"].check_in_member(m.id)
                    except Exception as e:  # noqa: BLE001
                        log.append("note: check-in skipped for %s (%s)" % (m.name, e))
        step("attendance history", make_attendance)

        def make_health():
            for i, (m, paid, po, ago) in enumerate(members):
                if env["otm.gym.health.profile"].search_count([("member_id", "=", m.id)]):
                    continue
                h = 158 + rnd.randint(0, 28)
                env["otm.gym.health.profile"].create({
                    "member_id": m.id, "height": h, "weight": round(h - 100 + rnd.randint(-5, 22), 1), "body_fat": round(rnd.uniform(14, 31), 1),
                    "blood_pressure": "120/80", "allergies": "None" if i % 3 else "Dust", "injuries": "" if i % 4 else "Mild knee strain",
                    "fitness_limitations": "", "emergency_information": "Contact family"})
            for m, paid, po, ago in members[:6]:
                if env["otm.gym.assessment"].search_count([("member_id", "=", m.id)]):
                    continue
                prof = env["otm.gym.health.profile"].search([("member_id", "=", m.id)], limit=1)
                for k, days in enumerate((60, 30, 3)):
                    a = env["otm.gym.assessment"].create({
                        "member_id": m.id, "date": today - timedelta(days=days), "assessor_id": users["demo.assessor"].id,
                        "height": prof.height, "weight": round(prof.weight + (2 - k) * 1.4, 1), "body_fat": round(prof.body_fat + (2 - k) * 0.8, 1),
                        "muscle_mass": round(30 + k * 0.6, 1), "chest": 38, "waist": round(34 - k * 0.7, 1), "hip": 38, "arm": 13, "thigh": 22,
                        "line_ids": [Command.create({"test_name": "Push-ups (1 min)", "result": str(20 + k * 4), "unit": "reps"}),
                                     Command.create({"test_name": "Plank", "result": str(60 + k * 20), "unit": "sec"})],
                        "notes": "Demo assessment", "recommendations": "Continue current plan"})
                    a.action_complete()
        step("health profiles and assessments", make_health)

        def make_plans():
            days = ["monday", "wednesday", "friday"]
            for m, paid, po, ago in members[:8]:
                if m.status != "active" or env["otm.gym.workout.plan"].search_count([("member_id", "=", m.id)]):
                    continue
                lines = [Command.create({"day": d, "day_title": ["Push", "Pull", "Legs"][i], "sequence": j, "exercise_id": ex.id,
                                         "sets": 3 + (j % 2), "reps": "10-12", "rest": 60})
                         for i, d in enumerate(days) for j, ex in enumerate(exercises[i * 4:i * 4 + 4])]
                w = env["otm.gym.workout.plan"].create({"member_id": m.id, "trainer_id": m.trainer_id.id, "goal": m.fitness_goal,
                                                        "start_date": today - timedelta(days=5), "end_date": today + timedelta(days=55), "line_ids": lines})
                w.action_submit(); w.action_approve(); w.action_activate()
                lines = [Command.create({"meal_type": mt, "food_id": foods[(i * 2 + j) % len(foods)].id, "quantity": 1})
                         for i, mt in enumerate(["breakfast", "lunch", "evening", "dinner"]) for j in range(2)]
                d = env["otm.gym.diet.plan"].create({"member_id": m.id, "nutritionist_id": users["demo.nutritionist"].id, "goal": m.fitness_goal,
                                                     "start_date": today - timedelta(days=5), "end_date": today + timedelta(days=55),
                                                     "calories_target": 2000, "protein_target": 110, "carb_target": 230, "fat_target": 60,
                                                     "water_target": 3, "line_ids": lines})
                d.action_submit(); d.action_approve(); d.action_activate()
        step("workout and diet plans", make_plans)

        def make_sessions():
            Ap = env["otm.gym.appointment"]
            if Ap.search_count([("member_id", "in", [x[0].id for x in members])]):
                return
            now = datetime.combine(today, time(0, 0))
            for i, (m, paid, po, ago) in enumerate(members[:8]):
                kind = ["personal_training", "assessment", "diet_consultation", "fitness_consultation"][i % 4]
                start = now + timedelta(days=(i % 4) - 1, hours=4 + i % 6)  # UTC-ish times, spread across yesterday..+2 days
                a = Ap.create({"member_id": m.id, "trainer_id": (trainers[i % len(trainers)] if kind == "personal_training" else
                                env["otm.gym.trainer"].search([("staff_role", "=", "assessor" if kind == "assessment" else "nutritionist")], limit=1) or trainers[0]).id,
                               "appointment_type": kind, "start_datetime": start, "duration_minutes": 60})
                if start > fields.Datetime.now():
                    a.action_confirm()
        step("appointments", make_sessions)
        return self._done(log)

    @api.model
    def _done(self, log):
        msg = "\n".join(log) + "\n\nLogins (password %s): %s, demo.member" % (PASSWORD, ", ".join(s[0] for s in STAFF))
        _logger.info("Gym demo data:\n%s", msg)
        failed = any(x.startswith("FAILED") for x in log)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Gym demo data"), "message": msg, "type": "warning" if failed else "success", "sticky": True}}
