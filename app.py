# -*- coding: utf-8 -*-
"""
Mesa de Ayuda (Help Desk) - versión web.  Flask + SQLAlchemy.

Variables de entorno:
  DATABASE_URL    URL de la base de datos (SQLite por defecto).
                  PostgreSQL: postgresql://usuario:clave@host:5432/db
                  MySQL:      mysql+pymysql://usuario:clave@host:3306/db
  SECRET_KEY      Clave para las sesiones (cámbiala en producción y NO la cambies
                  después, o se cerrarán todas las sesiones).
  ADMIN_USER      Usuario del panel técnico (por defecto: admin)
  ADMIN_PASSWORD  Clave del panel técnico   (por defecto: admin123)
  TZ_OFFSET       Horas de diferencia con UTC (por defecto: -5, Colombia)
"""
import csv
import hmac
import io
import os
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, flash, g, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

ESTADOS = ["En proceso", "Atendido", "Finalizado", "Escalado a dependencia superior"]
CLASES = {"En proceso": "proceso", "Atendido": "atendido", "Finalizado": "finalizado",
          "Escalado a dependencia superior": "escalado"}
PRIORIDADES = ["Baja", "Media", "Alta", "Urgente"]
CATEGORIAS = ["Hardware", "Software", "Red / Internet", "Correo electrónico",
              "Impresoras", "Accesos y contraseñas", "Otro"]
DEPENDENCIAS = ["Soporte Nivel 2", "Infraestructura", "Seguridad informática",
                "Desarrollo de software", "Gerencia de TI"]
LIMITES = {"nombre": 80, "apellido": 80, "direccion": 200, "telefono": 15,
           "email": 120, "asunto": 150}
ETIQUETAS = {"nombre": "Nombre", "apellido": "Apellido", "direccion": "Dirección",
             "telefono": "Teléfono", "email": "Correo electrónico", "asunto": "Asunto"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("RENDER"))  # HTTPS en Render

uri = os.environ.get("DATABASE_URL") or "sqlite:///" + os.path.join(BASE_DIR, "helpdesk.db")
if uri.startswith("postgres://"):  # Heroku / Render antiguos
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = uri
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
TZ_OFFSET = float(os.environ.get("TZ_OFFSET", "-5"))

db = SQLAlchemy(app)


def ahora():
    return datetime.now(timezone(timedelta(hours=TZ_OFFSET))).replace(tzinfo=None, microsecond=0)


# ------------------------------- Modelos -------------------------------
class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), nullable=False)
    apellido = db.Column(db.String(80), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    creado = db.Column(db.DateTime, nullable=False, default=ahora)
    tickets = db.relationship("Ticket", backref="usuario", order_by="Ticket.id.desc()")

    @property
    def nombre_completo(self):
        return f"{self.nombre} {self.apellido}"


class Ticket(db.Model):
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), unique=True, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True, index=True)
    nombre = db.Column(db.String(80), nullable=False)
    apellido = db.Column(db.String(80), nullable=False)
    direccion = db.Column(db.String(200), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    categoria = db.Column(db.String(40), nullable=False)
    prioridad = db.Column(db.String(20), nullable=False)
    asunto = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    estado = db.Column(db.String(40), nullable=False, default="En proceso", index=True)
    creado = db.Column(db.DateTime, nullable=False, default=ahora)
    actualizado = db.Column(db.DateTime, nullable=False, default=ahora)
    seguimientos = db.relationship("Seguimiento", backref="ticket",
                                   order_by="Seguimiento.id",
                                   cascade="all, delete-orphan")

    @property
    def cliente(self):
        return f"{self.nombre} {self.apellido}"


class Seguimiento(db.Model):
    __tablename__ = "seguimientos"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    estado = db.Column(db.String(40), nullable=False)
    responsable = db.Column(db.String(80))
    comentario = db.Column(db.Text)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora)


def preparar_base():
    """Crea las tablas y agrega la columna usuario_id si la base es de una versión anterior."""
    db.create_all()
    columnas = [c["name"] for c in inspect(db.engine).get_columns("tickets")]
    if "usuario_id" not in columnas:
        db.session.execute(text(
            "ALTER TABLE tickets ADD COLUMN usuario_id INTEGER REFERENCES usuarios(id)"))
        db.session.commit()


with app.app_context():
    preparar_base()


# ------------------------------- Sesión y permisos -------------------------------
def es_admin():
    return session.get("admin") is True


def usuario_actual():
    if "usuario" not in g:
        uid = session.get("uid")
        g.usuario = db.session.get(Usuario, uid) if uid else None
    return g.usuario


def iniciar_sesion(uid=None, admin=False, recordar=True):
    session.clear()
    if admin:
        session["admin"] = True
    else:
        session["uid"] = uid
    session.permanent = bool(recordar)  # True = la sesión dura 30 días


def destino_seguro(destino, por_defecto):
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return destino
    return por_defecto


def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not es_admin():
            flash("Inicia sesión como técnico para acceder al panel.", "warn")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return w


def cliente_required(f):
    @wraps(f)
    def w(*a, **kw):
        if es_admin():
            flash("Esta sección es para clientes. Entra con una cuenta de cliente.", "warn")
            return redirect(url_for("tickets"))
        if not usuario_actual():
            flash("Inicia sesión o crea tu cuenta para continuar.", "warn")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return w


@app.context_processor
def inyectar():
    return dict(ESTADOS=ESTADOS, CLASES=CLASES, PRIORIDADES=PRIORIDADES,
                CATEGORIAS=CATEGORIAS, DEPENDENCIAS=DEPENDENCIAS,
                es_admin=es_admin(), usuario=usuario_actual())


# ------------------------------- Utilidades -------------------------------
def validar_datos(d, campos):
    errores = []
    for k in campos:
        v = d.get(k, "")
        if not v:
            errores.append(f"El campo «{ETIQUETAS[k]}» es obligatorio.")
        elif len(v) > LIMITES[k]:
            errores.append(f"«{ETIQUETAS[k]}» admite máximo {LIMITES[k]} caracteres.")
    if d.get("telefono") and not re.fullmatch(r"[\d\s+\-()]{7,15}", d["telefono"]):
        errores.append("El teléfono solo puede contener números, +, -, ( ) (7 a 15 caracteres).")
    if d.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
        errores.append("El correo electrónico no es válido.")
    return errores


def crear_ticket(d, usuario=None):
    t = Ticket(**d, estado="En proceso", usuario_id=usuario.id if usuario else None)
    db.session.add(t)
    db.session.flush()
    t.codigo = f"TK-{t.creado.year}-{t.id:05d}"
    db.session.add(Seguimiento(ticket_id=t.id, estado="En proceso", responsable="Sistema",
                               comentario="Ticket registrado y en proceso de revisión."))
    db.session.commit()
    return t


# ------------------------------- Cuenta de cliente -------------------------------
@app.route("/")
def inicio():
    if es_admin():
        return redirect(url_for("tickets"))
    if usuario_actual():
        return redirect(url_for("nuevo"))
    return redirect(url_for("login"))


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if usuario_actual():
        return redirect(url_for("nuevo"))
    d = {}
    if request.method == "POST":
        d = {k: request.form.get(k, "").strip() for k in
             ("nombre", "apellido", "direccion", "telefono", "email")}
        d["email"] = d["email"].lower()
        clave, clave2 = request.form.get("clave", ""), request.form.get("clave2", "")
        errores = validar_datos(d, ("nombre", "apellido", "direccion", "telefono", "email"))
        if len(clave) < 6:
            errores.append("La contraseña debe tener al menos 6 caracteres.")
        elif clave != clave2:
            errores.append("Las contraseñas no coinciden.")
        if d["email"] and Usuario.query.filter_by(email=d["email"]).first():
            errores.append("Ya existe una cuenta con ese correo. Inicia sesión.")
        if errores:
            for e in errores:
                flash(e, "error")
        else:
            u = Usuario(**d, password_hash=generate_password_hash(clave, method="pbkdf2:sha256"))
            db.session.add(u)
            db.session.commit()
            iniciar_sesion(uid=u.id, recordar=True)
            flash(f"¡Bienvenido, {u.nombre}! Tu cuenta fue creada.", "ok")
            return redirect(url_for("nuevo"))
    return render_template("registro.html", d=d)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and (es_admin() or usuario_actual()):
        return redirect(url_for("inicio"))
    if request.method == "POST":
        ident = request.form.get("identificador", "").strip()
        clave = request.form.get("clave", "")
        recordar = request.form.get("recordar") == "on"
        siguiente = request.args.get("next")
        es_tecnico = (hmac.compare_digest(ident.encode(), ADMIN_USER.encode())
                      and hmac.compare_digest(clave.encode(), ADMIN_PASSWORD.encode()))
        if es_tecnico:
            iniciar_sesion(admin=True, recordar=recordar)
            return redirect(destino_seguro(siguiente, url_for("tickets")))
        u = Usuario.query.filter_by(email=ident.lower()).first()
        if u and check_password_hash(u.password_hash, clave):
            iniciar_sesion(uid=u.id, recordar=recordar)
            return redirect(destino_seguro(siguiente, url_for("nuevo")))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "ok")
    return redirect(url_for("login"))


@app.route("/perfil", methods=["GET", "POST"])
@cliente_required
def perfil():
    u = usuario_actual()
    if request.method == "POST":
        d = {k: request.form.get(k, "").strip() for k in
             ("nombre", "apellido", "direccion", "telefono")}
        nueva = request.form.get("clave", "")
        errores = validar_datos(d, ("nombre", "apellido", "direccion", "telefono"))
        if nueva and len(nueva) < 6:
            errores.append("La nueva contraseña debe tener al menos 6 caracteres.")
        if errores:
            for e in errores:
                flash(e, "error")
        else:
            for k, v in d.items():
                setattr(u, k, v)
            if nueva:
                u.password_hash = generate_password_hash(nueva, method="pbkdf2:sha256")
            db.session.commit()
            flash("Tus datos fueron actualizados. Se usarán en tus próximos tickets.", "ok")
            return redirect(url_for("perfil"))
    return render_template("perfil.html", u=u)


# ------------------------------- Tickets del cliente -------------------------------
@app.route("/nuevo", methods=["GET", "POST"])
@cliente_required
def nuevo():
    u = usuario_actual()
    d = {}
    if request.method == "POST":
        d = {k: request.form.get(k, "").strip() for k in
             ("categoria", "prioridad", "asunto", "descripcion")}
        errores = validar_datos(d, ("asunto",))
        if d["categoria"] not in CATEGORIAS:
            d["categoria"] = "Otro"
        if d["prioridad"] not in PRIORIDADES:
            d["prioridad"] = "Media"
        if errores:
            for e in errores:
                flash(e, "error")
        else:
            t = crear_ticket(dict(nombre=u.nombre, apellido=u.apellido, direccion=u.direccion,
                                  telefono=u.telefono, **d), usuario=u)
            flash(f"Ticket generado correctamente. Tu código es {t.codigo}", "ok")
            return redirect(url_for("seguimiento", codigo=t.codigo))
    return render_template("nuevo.html", d=d, u=u)


@app.route("/mis-tickets")
@cliente_required
def mis_tickets():
    return render_template("mis_tickets.html", lista=usuario_actual().tickets)


@app.route("/consulta", methods=["GET", "POST"])
def consulta():
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip().upper()
        if Ticket.query.filter_by(codigo=codigo).first():
            return redirect(url_for("seguimiento", codigo=codigo))
        flash("No encontramos un ticket con ese código.", "error")
    return render_template("consulta.html")


@app.route("/seguimiento/<codigo>")
def seguimiento(codigo):
    t = Ticket.query.filter_by(codigo=codigo.upper()).first_or_404()
    u = usuario_actual()
    # Datos completos solo para el técnico o el dueño del ticket
    ver_datos = es_admin() or (u is not None and t.usuario_id == u.id)
    return render_template("seguimiento.html", t=t, ver_datos=ver_datos)


# ------------------------------- Panel técnico -------------------------------
@app.route("/tickets")
@admin_required
def tickets():
    q = request.args.get("q", "").strip()
    estado = request.args.get("estado", "")
    query = Ticket.query
    if estado in ESTADOS:
        query = query.filter_by(estado=estado)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Ticket.codigo.ilike(like), Ticket.nombre.ilike(like),
                                    Ticket.apellido.ilike(like), Ticket.telefono.like(like),
                                    Ticket.asunto.ilike(like)))
    lista = query.order_by(Ticket.id.desc()).all()
    return render_template("tickets.html", lista=lista, q=q, estado=estado)


@app.route("/seguimiento/<codigo>/estado", methods=["POST"])
@admin_required
def cambiar_estado(codigo):
    t = Ticket.query.filter_by(codigo=codigo).first_or_404()
    if t.estado == "Finalizado":
        flash("El ticket está finalizado y ya no admite cambios.", "warn")
        return redirect(url_for("seguimiento", codigo=codigo))
    estado = request.form.get("estado", "")
    resp = request.form.get("responsable", "").strip()
    com = request.form.get("comentario", "").strip()
    dep = request.form.get("dependencia", "").strip()
    if estado not in ESTADOS or not resp or not com:
        flash("Estado, responsable y comentario son obligatorios.", "error")
        return redirect(url_for("seguimiento", codigo=codigo))
    if estado == "Escalado a dependencia superior":
        if dep not in DEPENDENCIAS:
            flash("Selecciona la dependencia a la que se escala.", "error")
            return redirect(url_for("seguimiento", codigo=codigo))
        com = f"[Escalado a: {dep}] {com}"
    t.estado = estado
    t.actualizado = ahora()
    db.session.add(Seguimiento(ticket_id=t.id, estado=estado, responsable=resp, comentario=com))
    db.session.commit()
    flash("Seguimiento registrado.", "ok")
    return redirect(url_for("seguimiento", codigo=codigo))


@app.route("/seguimiento/<codigo>/eliminar", methods=["POST"])
@admin_required
def eliminar(codigo):
    t = Ticket.query.filter_by(codigo=codigo).first_or_404()
    db.session.delete(t)
    db.session.commit()
    flash("Ticket eliminado.", "ok")
    return redirect(url_for("tickets"))


@app.route("/reportes")
@admin_required
def reportes():
    def conteo(col):
        return dict(db.session.query(col, db.func.count(Ticket.id)).group_by(col).all())
    total = Ticket.query.count()
    por_estado = conteo(Ticket.estado)
    return render_template("reportes.html", total=total, por_estado=por_estado,
                           por_prioridad=conteo(Ticket.prioridad),
                           por_categoria=conteo(Ticket.categoria),
                           maximo=max(por_estado.values(), default=1))


@app.route("/reportes.csv")
@admin_required
def reporte_csv():
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["Código", "Nombre", "Apellido", "Dirección", "Teléfono", "Categoría",
                "Prioridad", "Asunto", "Descripción", "Estado", "Creado", "Actualizado"])
    for t in Ticket.query.order_by(Ticket.id).all():
        w.writerow([t.codigo, t.nombre, t.apellido, t.direccion, t.telefono, t.categoria,
                    t.prioridad, t.asunto, t.descripcion, t.estado, t.creado, t.actualizado])
    nombre = f"reporte_tickets_{ahora():%Y%m%d_%H%M}.csv"
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={nombre}"})


@app.route("/demo", methods=["POST"])
@admin_required
def demo():
    ejemplos = [
        ("María", "Gómez", "Cra 5 # 10-20, Cartagena", "3001234567", "Hardware", "Alta",
         "El computador no enciende", "Se fue la luz y desde entonces no prende.",
         ("Atendido", "Técnico demo", "Se revisó la fuente de poder.")),
        ("Carlos", "Pérez", "Calle 30 # 8-15, Bocagrande", "3109876543", "Red / Internet", "Media",
         "Sin conexión a internet", "El WiFi aparece conectado pero no navega.",
         ("Escalado a dependencia superior", "Técnico demo",
          "[Escalado a: Infraestructura] Falla en el router principal.")),
        ("Laura", "Martínez", "Av. Pedro de Heredia 45-12", "3205551122", "Software", "Baja",
         "Instalar Office", "Necesito Word y Excel en mi equipo.",
         ("Finalizado", "Técnico demo", "Instalación completada.")),
        ("Andrés", "Torres", "Mz C Lt 4, Turbaco", "3157778899", "Correo electrónico", "Urgente",
         "No puedo enviar correos", "Sale error de servidor SMTP.", None),
    ]
    for n, a, dr, tel, cat, pri, asu, des, seg in ejemplos:
        t = crear_ticket(dict(nombre=n, apellido=a, direccion=dr, telefono=tel, categoria=cat,
                              prioridad=pri, asunto=asu, descripcion=des))
        if seg:
            t.estado = seg[0]
            db.session.add(Seguimiento(ticket_id=t.id, estado=seg[0], responsable=seg[1],
                                       comentario=seg[2]))
            db.session.commit()
    flash("Se agregaron 4 tickets de ejemplo.", "ok")
    return redirect(url_for("tickets"))


@app.route("/salud")
def salud():
    db.session.execute(db.text("SELECT 1"))
    return f"ok - base de datos: {db.engine.dialect.name}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
