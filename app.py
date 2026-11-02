from flask import Flask, render_template, request, redirect, url_for, abort, jsonify, make_response
import csv
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeSerializer
import random, os, secrets

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(16))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ------------------ MODELE ------------------

class Event(db.Model):
    __tablename__ = "events"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    table_capacity = db.Column(db.Integer, nullable=False, default=10)
    table_type = db.Column(db.String(16), nullable=False, default="round")  # round|rect
    table_count = db.Column(db.Integer, nullable=True)
    in_table_tries = db.Column(db.Integer, nullable=False, default=2500)
    cross_tries = db.Column(db.Integer, nullable=False, default=3000)
    rng_seed = db.Column(db.Integer, nullable=True)
    invite_token = db.Column(db.String(255), nullable=False, unique=True)
    guests = db.relationship("Guest", backref="event", cascade="all, delete-orphan")
    rules = db.relationship("Rule", backref="event", cascade="all, delete-orphan")
    tables = db.relationship("EventTable", backref="event", cascade="all, delete-orphan", order_by="EventTable.position")
    seat_locks = db.relationship("SeatLock", backref="event", cascade="all, delete-orphan")

class Guest(db.Model):
    __tablename__ = "guests"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    name = db.Column(db.String(160), nullable=False)
    likes_csv = db.Column(db.Text, default="")
    avoids_csv = db.Column(db.Text, default="")
    partner_id = db.Column(db.Integer, db.ForeignKey("guests.id"), nullable=True)

class Rule(db.Model):
    __tablename__ = "rules"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    kind = db.Column(db.String(20), nullable=False)  # same_table|separate|pin_table
    guest_a_id = db.Column(db.Integer, db.ForeignKey("guests.id"), nullable=False)
    guest_b_id = db.Column(db.Integer, db.ForeignKey("guests.id"), nullable=True)
    table_no = db.Column(db.Integer, nullable=True)  # 0-based indeks stołu

class EventTable(db.Model):
    __tablename__ = "event_tables"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    label = db.Column(db.String(80), nullable=True)
    capacity = db.Column(db.Integer, nullable=False, default=10)
    table_type = db.Column(db.String(16), nullable=False, default="round")

class SeatLock(db.Model):
    __tablename__ = "seat_locks"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("events.id"), nullable=False)
    guest_id = db.Column(db.Integer, db.ForeignKey("guests.id"), nullable=False)
    table_no = db.Column(db.Integer, nullable=False)   # 0-based
    seat_idx = db.Column(db.Integer, nullable=False)   # 0-based w obrębie stołu

# ------------------ LEKKA MIGRACJA ------------------

def ensure_schema():
    with db.engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('events')").fetchall()]
        if 'table_type' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN table_type VARCHAR(16) NOT NULL DEFAULT 'round'")
        if 'table_count' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN table_count INTEGER NULL")
        if 'in_table_tries' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN in_table_tries INTEGER NOT NULL DEFAULT 2500")
        if 'cross_tries' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN cross_tries INTEGER NOT NULL DEFAULT 3000")
        if 'rng_seed' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN rng_seed INTEGER NULL")
        if 'invite_token' not in cols:
            conn.exec_driver_sql("ALTER TABLE events ADD COLUMN invite_token VARCHAR(255) NOT NULL DEFAULT ''")

        cols_g = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info('guests')").fetchall()]
        if 'partner_id' not in cols_g:
            conn.exec_driver_sql("ALTER TABLE guests ADD COLUMN partner_id INTEGER NULL")

        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            kind VARCHAR(20) NOT NULL,
            guest_a_id INTEGER NOT NULL,
            guest_b_id INTEGER NULL,
            table_no INTEGER NULL
        )
        """)
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS event_tables (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            label VARCHAR(80) NULL,
            capacity INTEGER NOT NULL DEFAULT 10,
            table_type VARCHAR(16) NOT NULL DEFAULT 'round'
        )
        """)
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS seat_locks (
            id INTEGER PRIMARY KEY,
            event_id INTEGER NOT NULL,
            guest_id INTEGER NOT NULL,
            table_no INTEGER NOT NULL,
            seat_idx INTEGER NOT NULL
        )
        """)

# ------------------ UTILS/OPT ------------------

def norm(s): return (s or "").strip().lower()
def parse_list(s): return [x.strip() for x in (s or "").replace(";", ",").split(",") if x.strip()]
def clean_name(s: str) -> str:
    return " ".join((s or "").strip().split())  # usuń podwójne spacje, trim

def to_runtime_guest(g: Guest):
    return {
        "id": g.id,
        "name": g.name,
        "likes": {norm(x) for x in parse_list(g.likes_csv)},
        "avoids": {norm(x) for x in parse_list(g.avoids_csv)},
        "partner_id": g.partner_id,
    }

def build_units(people):
    id_map = {p["id"]: p for p in people}
    seen, units = set(), []
    for p in people:
        if p["id"] in seen: continue
        partner = id_map.get(p.get("partner_id"))
        if partner and partner["id"] not in seen:
            units.append({"members": [p, partner], "size": 2})
            seen.add(p["id"]); seen.add(partner["id"])
        else:
            units.append({"members": [p], "size": 1})
            seen.add(p["id"])
    return units

def expand_units_round(units):
    seq=[]; [seq.extend(u["members"]) for u in units]; return seq

def expand_units_rect(units, cap):
    top_len, bot_len = cap//2, cap - cap//2
    A,B=[],[]
    def try_add(row,u,limit):
        if len(row)+u["size"]<=limit: row.extend(u["members"]); return True
        return False
    for u in units:
        if not try_add(A,u,top_len): try_add(B,u,bot_len)
    return A,B

# ---- WAGI (dystans 1 i 2) ----
LIKE_1, AVOID_1 = 3, 5
LIKE_2, AVOID_2 = 1, 2

# ======= Scoring po krzesłach =======

def score_seated_round(seats, w1l=LIKE_1, w1a=AVOID_1, w2l=LIKE_2, w2a=AVOID_2):
    n=len(seats)
    def step_from(i, step):
        k=0; idx=i
        while k< n:
            idx=(idx+step) % n
            if seats[idx] is not None: return seats[idx]
            k+=1
        return None
    total=0; breakdown=[]
    for i,g in enumerate(seats):
        if g is None: continue
        L1=step_from(i,-1); R1=step_from(i,1)
        L2=step_from(i,-2); R2=step_from(i,2)
        likes,avoids=g.get("likes",set()), g.get("avoids",set())
        liked=avoided=0
        def upd(nei, wl, wa):
            nonlocal total, liked, avoided
            if nei and norm(nei["name"]) in likes:  total+=wl; liked+=1
            if nei and norm(nei["name"]) in avoids: total-=wa; avoided+=1
        upd(L1,w1l,w1a); upd(R1,w1l,w1a); upd(L2,w2l,w2a); upd(R2,w2l,w2a)
        breakdown.append({
            "name":g["name"], "left_neighbor":L1["name"] if L1 else "",
            "right_neighbor":R1["name"] if R1 else "", "opposite_neighbor":"",
            "liked_neighbors":liked, "avoided_neighbors":avoided
        })
    return total, breakdown

def score_seated_rect(top, bot, w1l=LIKE_1, w1a=AVOID_1, w2l=LIKE_2, w2a=AVOID_2):
    total=0; breakdown=[]
    def score_row(row, opp):
        nonlocal total, breakdown
        m=len(row)
        for i,g in enumerate(row):
            if g is None: continue
            L1=row[i-1] if i-1>=0 else None
            R1=row[i+1] if i+1<m else None
            L2=row[i-2] if i-2>=0 else None
            R2=row[i+2] if i+2<m else None
            O = opp[i] if i < len(opp) else None
            likes,avoids=g.get("likes",set()), g.get("avoids",set())
            liked=avoided=0
            def upd(nei, wl, wa):
                nonlocal total, liked, avoided
                if nei and norm(nei["name"]) in likes:  total+=wl; liked+=1
                if nei and norm(nei["name"]) in avoids: total-=wa; avoided+=1
            upd(L1,w1l,w1a); upd(R1,w1l,w1a); upd(O,w1l,w1a); upd(L2,w2l,w2a); upd(R2,w2l,w2a)
            breakdown.append({
                "name":g["name"],
                "left_neighbor":L1["name"] if L1 else "",
                "right_neighbor":R1["name"] if R1 else "",
                "opposite_neighbor":O["name"] if O else "",
                "liked_neighbors":liked, "avoided_neighbors":avoided
            })
    score_row(top, bot); score_row(bot, top)
    return total, breakdown

# ======= Budowa planu =======

def to_seat_arrays(unit_tables, specs):
    seated=[]
    for tbl,sp in zip(unit_tables, specs):
        cap=sp["capacity"]; ttype=sp["type"]
        people=[]; [people.extend(u["members"]) for u in tbl]
        people=people[:cap]
        if ttype=="round":
            seats=[None]*cap
            for i,p in enumerate(people): seats[i]=p
            seated.append({"type":"round","cap":cap,"seats":seats})
        else:
            top_len=cap//2; bot_len=cap-top_len
            top=[None]*top_len; bot=[None]*bot_len
            for i,p in enumerate(people):
                if i<top_len: top[i]=p
                else: bot[i-top_len]=p
            seated.append({"type":"rect","cap":cap,"top":top,"bot":bot})
    return seated

def apply_seat_locks(event_id, seated, locks, guests_by_id):
    def get_slot(tab, seat_idx):
        if tab["type"]=="round": return ("seats", seat_idx)
        top_len = len(tab["top"])
        if seat_idx<top_len: return ("top", seat_idx)
        return ("bot", seat_idx-top_len)
    def read_slot(tab, seat_idx):
        key, idx = get_slot(tab, seat_idx); return tab[key][idx]
    def write_slot(tab, seat_idx, val):
        key, idx = get_slot(tab, seat_idx); tab[key][idx]=val

    for lk in locks:
        if lk.event_id!=event_id: continue
        t_no, s_idx = lk.table_no, lk.seat_idx
        if t_no<0 or t_no>=len(seated): continue
        tab=seated[t_no]; cap=tab["cap"]
        if s_idx<0 or s_idx>=cap: continue
        g = guests_by_id.get(lk.guest_id)
        if not g: continue

        # zdejmij go z aktualnego miejsca
        for t in seated:
            if t["type"]=="round":
                for i,p in enumerate(t["seats"]):
                    if p and p["id"]==g["id"]: t["seats"][i]=None
            else:
                for arr in ("top","bot"):
                    for i,p in enumerate(t[arr]):
                        if p and p["id"]==g["id"]: t[arr][i]=None

        # jeśli krzesło zajęte — znajdź temu komuś inne
        occupant = read_slot(tab, s_idx)
        write_slot(tab, s_idx, g)
        if occupant:
            placed=False
            for si in range(cap):
                if read_slot(tab, si) is None:
                    write_slot(tab, si, occupant); placed=True; break
            if not placed:
                for tab2 in seated:
                    for si in range(tab2["cap"]):
                        key, idx = ("seats", si) if tab2["type"]=="round" else (("top",si) if si<len(tab2["top"]) else ("bot", si-len(tab2["top"])))
                        if tab2[key][idx] is None:
                            tab2[key][idx]=occupant; placed=True; break
                    if placed: break
    return seated

def breakdown_from_seated(seated):
    total=0; per_table_rows=[]; rect_pairs=[]
    for tab in seated:
        if tab["type"]=="round":
            s, br = score_seated_round(tab["seats"])
            total+=s
            for r in br:
                r["border_class"]="red-border" if r["avoided_neighbors"]>0 else ("green-border" if r["liked_neighbors"]>0 else "")
            per_table_rows.append({"score":s,"rows":br,"capacity":tab["cap"],"type":"round"})
            rect_pairs.append(None)
        else:
            s, br = score_seated_rect(tab["top"], tab["bot"])
            total+=s
            for r in br:
                r["border_class"]="red-border" if r["avoided_neighbors"]>0 else ("green-border" if r["liked_neighbors"]>0 else "")
            per_table_rows.append({"score":s,"rows":br,"capacity":tab["cap"],"type":"rect"})
            rect_pairs.append((tab["top"], tab["bot"]))
    return total, per_table_rows, rect_pairs

# ====== Optymalizacja =======

def optimize_units_order(units_order, cap, ttype, tries=2000, rng=None):
    if rng is None: rng=random.Random()
    cur=list(units_order); n=len(cur)
    if n<=1: return cur
    def score_units(order):
        seated = to_seat_arrays([order], [{"capacity":cap,"type":ttype}])[0]
        if ttype=="round": s,_ = score_seated_round(seated["seats"])
        else: s,_ = score_seated_rect(seated["top"], seated["bot"])
        return s
    best = score_units(cur)
    for _ in range(tries):
        i,j=rng.sample(range(n),2)
        cur[i],cur[j]=cur[j],cur[i]
        s = score_units(cur)
        if s<best:
            cur[i],cur[j]=cur[j],cur[i]
        else:
            best=s
    return cur

def optimize_with_specs(people, specs, in_tries=2000, cross_tries=3000, seed=None):
    rng=random.Random(seed)
    units=build_units(people)
    unit_tables=[[] for _ in specs]
    def size(tbl): return sum(u["size"] for u in tbl)

    for u in units:
        placed=False
        for i,sp in enumerate(specs):
            if size(unit_tables[i])+u["size"]<=sp["capacity"]:
                unit_tables[i].append(u); placed=True; break
        if not placed:
            base=(specs[-1] if specs else {"capacity":10,"type":"round"})
            specs.append(dict(base)); unit_tables.append([u])

    for i,sp in enumerate(specs):
        unit_tables[i]=optimize_units_order(unit_tables[i], sp["capacity"], sp["type"], tries=in_tries, rng=rng)

    if len(unit_tables)>=2:
        for _ in range(cross_tries):
            a,b=rng.sample(range(len(unit_tables)),2)
            if unit_tables[a] and unit_tables[b]:
                ia=rng.randrange(len(unit_tables[a])); ib=rng.randrange(len(unit_tables[b]))
                unit_tables[a][ia],unit_tables[b][ib]=unit_tables[b][ib],unit_tables[a][ia]
        for i,sp in enumerate(specs):
            unit_tables[i]=optimize_units_order(unit_tables[i], sp["capacity"], sp["type"], tries=max(300,in_tries//2), rng=rng)

    return unit_tables, specs

# ------------------ TOKEN / ROUTES ------------------

def event_token(event_id:int)->str:
    s=URLSafeSerializer(app.config["SECRET_KEY"], salt="invite")
    return s.dumps({"e":event_id})

def load_event_by_token(token:str):
    s=URLSafeSerializer(app.config["SECRET_KEY"], salt="invite")
    try:
        data=s.loads(token); return db.session.get(Event, int(data.get("e")))
    except Exception: return None

def _build_specs(ev:Event):
    db_tables=EventTable.query.filter_by(event_id=ev.id).order_by(EventTable.position).all()
    specs=[{"capacity":t.capacity,"type":t.table_type,"label":t.label} for t in db_tables]
    default_spec={"capacity":ev.table_capacity,"type":ev.table_type}
    if not specs and ev.table_count: specs=[dict(default_spec) for _ in range(ev.table_count)]
    return specs, default_spec, db_tables

def _compute_plan(ev:Event):
    specs, default_spec, db_tables=_build_specs(ev)
    people=[to_runtime_guest(g) for g in ev.guests]
    guests_by_id={p["id"]:p for p in people}
    seated=[]; total=0; per_table_data=[]; rect_pairs=[]

    if people or specs:
        unit_tables, specs=optimize_with_specs(people, specs, ev.in_table_tries, ev.cross_tries, ev.rng_seed)
        seated = to_seat_arrays(unit_tables, specs)
        locks = SeatLock.query.filter_by(event_id=ev.id).all()
        seated = apply_seat_locks(ev.id, seated, locks, guests_by_id)
        total, per_table_data, rect_pairs = breakdown_from_seated(seated)

    multi_tables=[]
    for tab in seated:
        if tab["type"]=="round":
            multi_tables.append([g["name"] for g in tab["seats"] if g])
        else:
            multi_tables.append([g["name"] for g in tab["top"] if g] + [g["name"] for g in tab["bot"] if g])

    return specs, db_tables, multi_tables, per_table_data, rect_pairs, total, seated
def find_guest_by_name(event_id:int, name:str):
    if not name: return None
    return Guest.query.filter(
        Guest.event_id==event_id,
        db.func.lower(Guest.name)==db.func.lower(name.strip())
    ).first()

@app.route("/")
def events_index():
    events=Event.query.order_by(Event.id.desc()).all()
    return render_template("events_index.html", events=events)

@app.route("/events/create", methods=["POST"])
def create_event():
    name=request.form.get("name","").strip()
    capacity=request.form.get("capacity", type=int) or 10
    ttype=request.form.get("table_type","round")
    if ttype not in ("round","rect"): ttype="round"
    if not name: return redirect(url_for("events_index"))
    ev=Event(name=name, table_capacity=max(2,capacity), table_type=ttype, invite_token="")
    db.session.add(ev); db.session.commit()
    ev.invite_token=event_token(ev.id); db.session.commit()
    return redirect(url_for("event_detail", event_id=ev.id))

@app.route("/events/<int:event_id>")
def event_detail(event_id):
    ev=db.session.get(Event, event_id)
    if not ev: abort(404)
    if not ev.invite_token: ev.invite_token=event_token(ev.id); db.session.commit()
    specs, db_tables, multi_tables, per_table_data, rect_rows, total, seated=_compute_plan(ev)
    invite_url=url_for("public_invite", token=ev.invite_token, _external=True)
    return render_template("event_detail.html", ev=ev, guests=ev.guests, partner_choices=ev.guests,
                           multi_tables=multi_tables, per_table_data=per_table_data, rect_rows=rect_rows,
                           total_score=total, invite_url=invite_url, rules=ev.rules, db_tables=db_tables, seated=seated)


@app.route("/events/<int:event_id>/config", methods=["POST"])
def event_config(event_id):
    ev=db.session.get(Event, event_id); 
    if not ev: abort(404)
    ev.table_capacity=max(2, request.form.get("capacity", type=int) or ev.table_capacity)
    seed=(request.form.get("seed","").strip())
    ev.rng_seed=int(seed) if seed.isdigit() else None
    ttype=request.form.get("table_type", ev.table_type)
    ev.table_type= ttype if ttype in ("round","rect") else ev.table_type
    tc=(request.form.get("table_count","").strip())
    ev.table_count=int(tc) if tc.isdigit() and int(tc)>0 else None
    db.session.commit(); return redirect(url_for("event_detail", event_id=event_id))

# --- stoły CRUD ---
@app.route("/events/<int:event_id>/tables/add", methods=["POST"])
def add_tables(event_id):
    ev=db.session.get(Event, event_id); 
    if not ev: abort(404)
    count=request.form.get("count", type=int) or 0
    cap=request.form.get("capacity", type=int) or ev.table_capacity
    ttype=request.form.get("table_type") or ev.table_type
    label_prefix=(request.form.get("label_prefix") or "").strip() or "Stół"
    start=(db.session.query(db.func.max(EventTable.position)).filter_by(event_id=event_id).scalar() or -1)+1
    for i in range(count):
        db.session.add(EventTable(event_id=event_id, position=start+i, capacity=max(2,cap),
                                  table_type=("rect" if ttype=="rect" else "round"),
                                  label=f"{label_prefix} {start+i+1}"))
    db.session.commit(); return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/tables/<int:tid>/update", methods=["POST"])
def update_table(event_id, tid):
    t=db.session.get(EventTable, tid)
    if not t or t.event_id!=event_id: abort(404)
    t.capacity=max(2, request.form.get("capacity", type=int) or t.capacity)
    tp=request.form.get("table_type")
    if tp in ("round","rect"): t.table_type=tp
    t.label=(request.form.get("label") or "").strip() or None
    pos=request.form.get("position", type=int)
    if pos is not None and pos>=0: t.position=pos
    db.session.commit(); return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/tables/<int:tid>/delete", methods=["POST"])
def delete_table(event_id, tid):
    t=db.session.get(EventTable, tid)
    if t and t.event_id==event_id: db.session.delete(t); db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

# --- goście/partner ---
def _set_partner_bidirectional(event_id:int, g:Guest, partner_id:int|None):
    if g.partner_id and g.partner_id!=partner_id:
        old=db.session.get(Guest, g.partner_id)
        if old and old.event_id==event_id and old.partner_id==g.id: old.partner_id=None
    g.partner_id=partner_id
    if partner_id:
        p=db.session.get(Guest, partner_id)
        if p and p.event_id==event_id:
            if p.partner_id and p.partner_id!=g.id:
                other=db.session.get(Guest, p.partner_id)
                if other and other.event_id==event_id and other.partner_id==p.id: other.partner_id=None
            p.partner_id=g.id

@app.route("/events/<int:event_id>/guests/add", methods=["POST"])
def event_add_guest(event_id):
    ev=db.session.get(Event, event_id); 
    if not ev: abort(404)
    name=request.form.get("name","").strip()
    if name:
        g=Guest(event_id=event_id, name=name, likes_csv=request.form.get("want_next_to",""),
                avoids_csv=request.form.get("not_next_to",""))
        db.session.add(g); db.session.flush()
        pid=request.form.get("partner_id", type=int)
        if pid: _set_partner_bidirectional(event_id, g, pid)
        db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/guests/<int:gid>/delete", methods=["POST"])
def event_delete_guest(event_id,gid):
    g=db.session.get(Guest,gid)
    if g and g.event_id==event_id:
        if g.partner_id:
            p=db.session.get(Guest,g.partner_id)
            if p and p.event_id==event_id and p.partner_id==g.id: p.partner_id=None
        db.session.query(SeatLock).filter_by(event_id=event_id, guest_id=g.id).delete()
        db.session.delete(g); db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/guests/<int:gid>/edit", methods=["GET","POST"])
def event_edit_guest(event_id,gid):
    g=db.session.get(Guest,gid)
    if not g or g.event_id!=event_id: abort(404)
    if request.method=="POST":
        g.name=request.form.get("name","").strip()
        g.likes_csv=request.form.get("want_next_to","")
        g.avoids_csv=request.form.get("not_next_to","")
        pid=request.form.get("partner_id", type=int)
        _set_partner_bidirectional(event_id, g, pid if pid else None)
        db.session.commit(); return redirect(url_for("event_detail", event_id=event_id))
    return render_template("edit_guest.html", guest={"id":g.id,"name":g.name,"likes_csv":g.likes_csv,"avoids_csv":g.avoids_csv,"partner_id":g.partner_id},
                           partner_choices=Guest.query.filter_by(event_id=event_id).all(),
                           back_url=url_for("event_detail", event_id=event_id))

# --- reguły twarde ---
@app.route("/events/<int:event_id>/rules/add", methods=["POST"])
def event_add_rule(event_id):
    ev=db.session.get(Event,event_id)
    if not ev: abort(404)
    kind=request.form.get("kind")
    a=request.form.get("guest_a_id", type=int)
    b=request.form.get("guest_b_id", type=int)
    tno=request.form.get("table_no", type=int)
    if kind not in ("same_table","separate","pin_table") or not a:
        return redirect(url_for('event_detail', event_id=event_id))
    r=Rule(event_id=event_id, kind=kind, guest_a_id=a,
           guest_b_id=b if kind in ("same_table","separate") else None,
           table_no=tno if kind=="pin_table" else None)
    db.session.add(r); db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/rules/<int:rid>/delete", methods=["POST"])
def event_delete_rule(event_id, rid):
    r=db.session.get(Rule, rid)
    if r and r.event_id==event_id: db.session.delete(r); db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

@app.route("/events/<int:event_id>/regenerate_invite", methods=["POST"])
def regenerate_invite(event_id):
    ev=db.session.get(Event,event_id)
    if not ev: abort(404)
    ev.invite_token=event_token(ev.id); db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

# --- PUBLIC: formularz z partnerem PRO ---
@app.route("/invite/<token>", methods=["GET","POST"])
def public_invite(token):
    ev = load_event_by_token(token)
    if not ev: abort(404)

    if request.method == "POST":
        name   = clean_name(request.form.get("name") or "")
        likes  = request.form.get("want_next_to","")
        avoids = request.form.get("not_next_to","")

        partner_name   = clean_name(request.form.get("partner_name") or "")
        partner_likes  = request.form.get("partner_want_next_to","")
        partner_avoids = request.form.get("partner_not_next_to","")

        if name:
            # główny gość (anti-dup)
            g = find_guest_by_name(ev.id, name)
            if not g:
                g = Guest(event_id=ev.id, name=name)
                db.session.add(g); db.session.flush()
            g.likes_csv, g.avoids_csv = likes, avoids

            if partner_name and partner_name.lower()!=name.lower():
                p = find_guest_by_name(ev.id, partner_name)
                if not p:
                    p = Guest(event_id=ev.id, name=partner_name,
                              likes_csv=partner_likes, avoids_csv=partner_avoids)
                    db.session.add(p); db.session.flush()
                else:
                    # aktualizuj preferencje partnera tylko jeśli podane
                    if partner_likes:  p.likes_csv  = partner_likes
                    if partner_avoids: p.avoids_csv = partner_avoids

                _set_partner_bidirectional(ev.id, g, p.id)

            db.session.commit()
            return render_template("public_success.html", ev=ev)

    return render_template("public_form.html", ev=ev, token=token) @app.route("/events/<int:event_id>/export.csv")
def export_csv(event_id):
    ev=db.session.get(Event,event_id)
    if not ev: abort(404)
    output=[]
    header=["guest_id","name","partner_id","partner_name","likes","avoids"]
    output.append(header)

    # zbuduj mapę partnerów szybko
    id2guest={g.id:g for g in ev.guests}
    for g in ev.guests:
        partner_name = id2guest[g.partner_id].name if g.partner_id and g.partner_id in id2guest else ""
        row=[g.id, g.name, g.partner_id or "", partner_name, g.likes_csv or "", g.avoids_csv or ""]
        output.append(row)

    si=make_response()
    writer = csv.writer(si.stream)
    for r in output: writer.writerow(r)
    si.headers["Content-Disposition"]=f"attachment; filename=event_{event_id}_guests.csv"
    si.headers["Content-Type"]="text/csv; charset=utf-8"
    return si
@app.route("/events/<int:event_id>/recompute")
def recompute(event_id):
    ev=db.session.get(Event, event_id)
    if not ev: abort(404)
    # zwykły redirect; plan i tak liczymy „on the fly”
    return redirect(url_for("event_detail", event_id=event_id))


# --- pomocnicze do DnD/grup ---
def upsert_seat_lock(event_id:int, guest_id:int, table_no:int, seat_idx:int):
    db.session.query(SeatLock).filter_by(event_id=event_id, guest_id=guest_id).delete()
    db.session.add(SeatLock(event_id=event_id, guest_id=guest_id, table_no=table_no, seat_idx=seat_idx))
    db.session.query(Rule).filter_by(event_id=event_id, kind="pin_table", guest_a_id=guest_id).delete()

def upsert_pin_table(event_id:int, guest_id:int, table_no:int):
    r=Rule.query.filter_by(event_id=event_id, kind="pin_table", guest_a_id=guest_id).first()
    if r: r.table_no=table_no
    else: db.session.add(Rule(event_id=event_id, kind="pin_table", guest_a_id=guest_id, table_no=table_no))
    db.session.query(SeatLock).filter_by(event_id=event_id, guest_id=guest_id).delete()

def get_group_ids(event_id:int, guest_id:int):
    g=db.session.get(Guest, guest_id)
    if not g or g.event_id!=event_id: return [guest_id]
    ids=[g.id]
    if g.partner_id:
        p=db.session.get(Guest, g.partner_id)
        if p and p.event_id==event_id and p.id not in ids: ids.append(p.id)
    rev=db.session.query(Guest).filter_by(event_id=event_id, partner_id=g.id).all()
    for r in rev:
        if r.id not in ids: ids.append(r.id)
    if ids[0]!=guest_id:
        ids.remove(guest_id); ids.insert(0, guest_id)
    return ids

# --- DRAG&DROP API (grupowe) ---
@app.route("/events/<int:event_id>/move_guest", methods=["POST"])
def move_guest(event_id):
    ev=db.session.get(Event,event_id)
    if not ev: abort(404)
    data=request.get_json(silent=True) or {}
    gid=int(data.get("guest_id",0))
    table_no=int(data.get("target_table",0))  # 1-based z UI
    seat_idx=data.get("target_seat", None)
    if gid<=0 or table_no<=0: return jsonify({"ok":False,"error":"bad_params"}),400
    t0=table_no-1

    specs, default_spec, db_tables=_build_specs(ev)
    if t0<0 or t0>=len(specs): return jsonify({"ok":False,"error":"table_out_of_range"}),400
    cap=specs[t0]["capacity"]

    group_ids = get_group_ids(event_id, gid)

    if seat_idx is not None and isinstance(seat_idx,int) and seat_idx>=0:
        for k,mid in enumerate(group_ids):
            si = (seat_idx + k) % cap
            upsert_seat_lock(event_id, mid, t0, si)
        db.session.commit()
    else:
        for mid in group_ids:
            upsert_pin_table(event_id, mid, t0)
        db.session.commit()

    specs, db_tables, multi_tables, per_table_data, rect_rows, total, seated=_compute_plan(ev)
    html=render_template("partials_plan.html", ev=ev, multi_tables=multi_tables, per_table_data=per_table_data,
                         rect_rows=rect_rows, total_score=total, seated=seated)
    return jsonify({"ok":True,"html":html})

@app.route("/events/<int:event_id>/unlock_guest", methods=["POST"])
def unlock_guest(event_id):
    gid=request.form.get("guest_id", type=int)
    if not gid: abort(400)
    db.session.query(SeatLock).filter_by(event_id=event_id, guest_id=gid).delete()
    db.session.query(Rule).filter_by(event_id=event_id, kind="pin_table", guest_a_id=gid).delete()
    db.session.commit()
    return redirect(url_for("event_detail", event_id=event_id))

with app.app_context():
    db.create_all()
    ensure_schema()

if __name__=="__main__":
    app.run(debug=True)
