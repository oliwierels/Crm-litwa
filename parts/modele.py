# Część crm.py — wspólna dla CRM PL i CRM LT/DE (ten sam plik w obu repozytoriach; zmiana tutaj = zmiana w obu).
# Wczytywana przez _include("modele") w tym miejscu crm.py, w którym ten kod był wcześniej — działa w przestrzeni
# nazw crm.py (te same modele, ustawienia i funkcje), więc kolejność i zachowanie są takie jak przed podziałem.
# Temat: modele bazy: dni wynajmu, zadania i komentarze, historia etapów, operatorzy, roboty, ekipy, ustawienia

class LeadEventDate(db.Model):
    __tablename__ = "lead_event_dates"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    location = db.Column(db.String(200), nullable=True)  # nadpisanie lokalizacji tylko na ten dzień

    lead = db.relationship(
        "Lead",
        backref=db.backref("event_days", cascade="all, delete-orphan", order_by="LeadEventDate.event_date"),
    )


class Activity(db.Model):
    __tablename__ = "activities"
    id = db.Column(db.Integer, primary_key=True)
    # nullable: zadanie bez leada = "zadanie własne", niezwiązane z żadną firmą
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=True)
    kind = db.Column(db.String(20), nullable=False)  # call | email | online | meeting | note
    due_date = db.Column(db.DateTime, nullable=True)
    done = db.Column(db.Boolean, default=False)
    done_at = db.Column(db.DateTime)
    assigned_to = db.Column(db.Integer, default=1)
    notes = db.Column(db.Text)
    voice_note = db.Column(db.Text, nullable=True)  # data: URL nagranej wiadomości głosowej (komentarze)
    outcome = db.Column(db.String(20), nullable=True)  # wynik kontaktu: odebral | nie_odebral | odpisal | bez_odpowiedzi
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # kto utworzył / zlecił zadanie albo napisał komentarz (osoba); None = automat CRM. Zadanie zlecone przez inną
    # osobę niż wykonawca (created_by != assigned_to) trzeba rozliczyć w raporcie dnia.
    created_by = db.Column(db.Integer, nullable=True)


class ActivityComment(db.Model):
    __tablename__ = "activity_comments"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.Integer, db.ForeignKey("activities.id"), nullable=False)
    author_id = db.Column(db.Integer)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    activity = db.relationship(
        "Activity",
        backref=db.backref("comments", cascade="all, delete-orphan", order_by="ActivityComment.created_at"),
    )


class StageHistory(db.Model):
    __tablename__ = "stage_history"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    from_stage = db.Column(db.String(50), nullable=True)  # None = lead utworzony
    to_stage = db.Column(db.String(50), nullable=False)
    changed_by = db.Column(db.Integer)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    reason = db.Column(db.String(30), nullable=True)  # powód straty / potwierdzenie wygranej przy tej zmianie
    note = db.Column(db.Text, nullable=True)

    lead = db.relationship(
        "Lead",
        backref=db.backref("stage_history", cascade="all, delete-orphan", order_by="StageHistory.changed_at"),
    )


# --- LOGISTYKA: kto (operator) z czym (robot) jedzie na który dzień realizacji ---

class Operator(db.Model):
    __tablename__ = "logistics_operators"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_city = db.Column(db.String(200))  # skąd wyjeżdża; puste = baza domyślna
    phone = db.Column(db.String(50))
    active = db.Column(db.Boolean, default=True)
    # konto w CRM (id z USERS), żeby operator widział swoje zlecenia na Dashboardzie;
    # None = dopasuj po imieniu, 0 = celowo bez konta
    user_id = db.Column(db.Integer, nullable=True)


class RobotUnit(db.Model):
    __tablename__ = "logistics_robots"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    model = db.Column(db.String(10), nullable=False, default="G1")  # G1 | B2
    active = db.Column(db.Boolean, default=True)


class Crew(db.Model):
    """Ekipa na jeden dzień realizacji: operator + robot, a ekipa podwójna (robots_count=2)
    to dwóch operatorów i dwa roboty w tym samym miejscu. Kilka ekip tego samego dnia
    = równoległe lokalizacje (np. dwa kongresy naraz)."""
    __tablename__ = "logistics_crews"
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    event_date = db.Column(db.Date, nullable=False)
    operator_id = db.Column(db.Integer, db.ForeignKey("logistics_operators.id"), nullable=True)
    robot_id = db.Column(db.Integer, db.ForeignKey("logistics_robots.id"), nullable=True)
    robots_count = db.Column(db.Integer, default=1)  # 2 = ekipa podwójna
    operator2_id = db.Column(db.Integer, db.ForeignKey("logistics_operators.id"), nullable=True)
    robot2_id = db.Column(db.Integer, db.ForeignKey("logistics_robots.id"), nullable=True)
    city = db.Column(db.String(200))  # inne miejsce niż w leadzie (np. druga ekipa w innym mieście)
    notes = db.Column(db.String(300))

    lead = db.relationship("Lead", backref=db.backref("crews", cascade="all, delete-orphan"))
    operator = db.relationship("Operator", foreign_keys=[operator_id])
    robot = db.relationship("RobotUnit", foreign_keys=[robot_id])
    operator2 = db.relationship("Operator", foreign_keys=[operator2_id])
    robot2 = db.relationship("RobotUnit", foreign_keys=[robot2_id])

    @property
    def is_double(self):
        return (self.robots_count or 1) >= 2

    def slot_operator_ids(self):
        """[(miejsce 1|2, id operatora albo None)] — w ekipie pojedynczej tylko miejsce 1."""
        return [(1, self.operator_id)] + ([(2, self.operator2_id)] if self.is_double else [])

    def operator_ids(self):
        return [oid for _, oid in self.slot_operator_ids() if oid]

    def operators_list(self):
        return [o for o in (self.operator, self.operator2 if self.is_double else None) if o]

    def robot_ids(self):
        return [rid for rid in (self.robot_id, self.robot2_id if self.is_double else None) if rid]

    def robots_list(self):
        return [r for r in (self.robot, self.robot2 if self.is_double else None) if r]


class AppSetting(db.Model):
    __tablename__ = "app_settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)
