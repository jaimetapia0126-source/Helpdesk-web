# -*- coding: utf-8 -*-
"""
Mesa de Ayuda (Help Desk) - versión web.  Flask + SQLAlchemy.

Variables de entorno:
  DATABASE_URL    URL de la base de datos (SQLite por defecto).
                  PostgreSQL: postgresql://usuario:clave@host:5432/db
                  MySQL:      mysql+pymysql://usuario:clave@host:3306/db
  SECRET_KEY      Clave para las sesiones (cámbiala en producción).
  ADMIN_USER      Usuario del panel (por defecto: admin)
  ADMIN_PASSWORD  Clave del panel   (por defecto: admin123)
  TZ_OFFSET       Horas de diferencia con UTC (por defecto: -5, Colombia)
"""
import csv
import io
import os
import re
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from flask_sqlalchemy import SQLAlchemy

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

ESTADOS = ["En proceso", "Atendido", "Finalizado", "Escalado a dependencia superior"]
CLASES = {"En proceso": "proceso", "Atendido": "atendido", "Finalizado": "finalizado",
          "Escalado a dependencia superior": "escalado"}
PRIORIDADES = ["Baja", "Media", "Alta", "Urgente"]
CATEGORIAS = ["Hardware", "Software", "Red / Internet", "Correo electrónico",
              "Impresoras", "Accesos y contraseñas", "Otro"]
DEPENDENCIAS = ["Soporte Nivel 2", "Infraestructura", "Seguridad informática",
                "Desarrollo de software", "Gerencia de TI"]

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

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
class Ticket(db.Model):
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(20), unique=True, index=True)
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


with app.app_context():
    db.create_all()


# ------------------------------- Utilidades -------------------------------
def es_admin():
    return session.get("admin") is True


def admin_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not es_admin():
            flash("Inicia sesión para acceder al panel.", "warn")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return w


@app.context_processor
def inyectar():
    return dict(ESTADOS=ESTADOS, CLASES=CLASES, PRIORIDADES=PRIORIDADES,
                CATEGORIAS=CATEGORIAS, DEPENDENCIAS=DEPENDENCIAS, es_admin=es_admin())


def crear_ticket(d):
    t = Ticket(**d, estado="En proceso")
    db.session.add(t)
    db.session.flush()
    t.codigo = f"TK-{t.creado.year}-{t.id:05d}"
    db.session.add(Seguimiento(ticket_id=t.id, estado="En proceso", responsable="Sistema",
                               comentario="Ticket registrado y en proceso de revisión."))
    db.session.commit()
    return t


# ------------------------------- Públicas -------------------------------
@app.route("/")
def inicio():
    return redirect(url_for("nuevo"))


@app.route("/nuevo", methods=["GET", "POST"])
def nuevo():
    datos = {}
    if request.method == "POST":
        datos = {k: request.form.get(k, "").strip() for k in
                 ("nombre", "apellido", "direccion", "telefono", "categoria",
                  "prioridad", "asunto", "descripcion")}
        etiquetas = {"nombre": "Nombre", "apellido": "Apellido", "direccion": "Dirección",
                     "telefono": "Teléfono", "asunto": "Asunto"}
        errores = [f"El campo «{n}» es obligatorio." for k, n in etiquetas.items() if not datos[k]]
        if datos["telefono"] and not re.fullmatch(r"[\d\s+\-()]{7,15}", datos["telefono"]):
            errores.append("El teléfono solo puede contener números, +, -, ( ) (7 a 15 caracteres).")
        if datos["categoria"] not in CATEGORIAS:
            datos["categoria"] = "Otro"
        if datos["prioridad"] not in PRIORIDADES:
            datos["prioridad"] = "Media"
        if errores:
            for e in errores:
                flash(e, "error")
        else:
            t = crear_ticket(datos)
            flash(f"Ticket generado correctamente. Tu código es {t.codigo}", "ok")
            return redirect(url_for("seguimiento", codigo=t.codigo))
    return render_template("nuevo.html", d=datos)


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
    return render_template("seguimiento.html", t=t)


# ------------------------------- Panel (admin) -------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if (request.form.get("usuario") == ADMIN_USER
                and request.form.get("clave") == ADMIN_PASSWORD):
            session["admin"] = True
            destino = request.args.get("next") or url_for("tickets")
            return redirect(destino if destino.startswith("/") else url_for("tickets"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "ok")
    return redirect(url_for("nuevo"))


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
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
