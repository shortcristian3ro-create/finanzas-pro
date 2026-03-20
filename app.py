from flask import Flask, render_template, request, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import urllib.parse

app = Flask(__name__)
app.secret_key = "clave_secreta_pro_2026"

# --- CONFIGURACIÓN DE BASE DE DATOS (VERSIÓN ROBUSTA PARA SUPABASE) ---
uri = os.environ.get("DATABASE_URL")

if uri:
    # 1. Limpieza de espacios y ajuste de protocolo
    uri = uri.strip()
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    
    # 2. Manejo especial para contraseñas con símbolos como $
    # Si la URL contiene el símbolo $ sin codificar, lo corregimos
    if "$" in uri and "%24" not in uri:
        # Extraemos las partes para codificar solo la contraseña
        try:
            # Reemplazamos el $ por su código seguro %24
            uri = uri.replace("$", "%24")
        except Exception as e:
            print(f"Error procesando URI: {e}")

    app.config['SQLALCHEMY_DATABASE_URI'] = uri
else:
    # Configuración local para tu PC
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, 'instance', 'usuarios.db')
    if not os.path.exists(os.path.join(basedir, 'instance')):
        os.makedirs(os.path.join(basedir, 'instance'))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODELOS ---
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(80), nullable=False)
    meta_ahorro = db.Column(db.Float, default=1000.0)
    movimientos = db.relationship('Movimiento', backref='dueno', lazy=True)
    pendientes = db.relationship('Pendiente', backref='dueno', lazy=True)
    fijos = db.relationship('GastoFijo', backref='dueno', lazy=True)

class Movimiento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200)); monto = db.Column(db.Float)
    tipo = db.Column(db.String(10)); categoria = db.Column(db.String(50))
    fecha = db.Column(db.DateTime, default=datetime.now)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))

class Pendiente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200)); monto = db.Column(db.Float)
    pagado = db.Column(db.Boolean, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))

class GastoFijo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    descripcion = db.Column(db.String(200)); monto = db.Column(db.Float)
    dia_cobro = db.Column(db.Integer)
    pagado_este_mes = db.Column(db.Boolean, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))

with app.app_context():
    db.create_all()

# --- RUTAS ---

@app.route("/", methods=["GET", "POST"])
def login():
    mensaje = None; color = "red"
    if request.method == "POST":
        nombre = request.form.get("usuario").strip().lower()
        password = request.form.get("password").strip()
        accion = request.form.get("accion")
        user = Usuario.query.filter_by(nombre=nombre).first()
        if accion == "registro":
            if user: mensaje = f"❌ El usuario '{nombre}' ya existe."
            else:
                db.session.add(Usuario(nombre=nombre, password=password))
                db.session.commit()
                mensaje = f"✨ ¡Usuario '{nombre}' creado! Inicia sesión."; color = "#2ecc71"
        elif accion == "login":
            if user and user.password == password:
                session['u_id'] = user.id; session['u_nombre'] = user.nombre
                return redirect(url_for('dashboard'))
            mensaje = "❌ Usuario o contraseña incorrectos."
    return render_template("index.html", mensaje=mensaje, color_alerta=color)

@app.route("/dashboard")
def dashboard():
    if 'u_id' not in session: return redirect(url_for('login'))
    user = Usuario.query.get(session['u_id'])
    movs = Movimiento.query.filter_by(usuario_id=user.id).all()
    pends_no_pagados = Pendiente.query.filter_by(usuario_id=user.id, pagado=False).all()
    fijos = GastoFijo.query.filter_by(usuario_id=user.id).all()
    
    t_ingresos = sum(m.monto for m in movs if m.tipo == 'Ingreso')
    t_gastos = sum(m.monto for m in movs if m.tipo == 'Gasto')
    t_ahorros = sum(m.monto for m in movs if m.categoria == 'Ahorro')
    t_deudas_pend = sum(p.monto for p in pends_no_pagados)
    
    saldo = t_ingresos - t_gastos - t_ahorros
    progreso = min((t_ahorros / user.meta_ahorro) * 100, 100) if user.meta_ahorro > 0 else 0
    alertas = [f for f in fijos if 0 <= (f.dia_cobro - datetime.now().day) <= 7 and not f.pagado_este_mes]
    
    return render_template("dashboard.html", nombre=user.nombre, saldo=saldo, 
                           deudas=t_deudas_pend, ahorros=t_ahorros, meta=user.meta_ahorro, progreso=progreso, alertas=alertas)

@app.route("/pagar/<tipo>/<int:id>")
def pagar(tipo, id):
    if 'u_id' not in session: return redirect(url_for('login'))
    uid = session['u_id']
    if tipo == "pendiente":
        item = Pendiente.query.get(id)
        if item and not item.pagado:
            item.pagado = True
            db.session.add(Movimiento(descripcion=f"PAGO DEUDA: {item.descripcion}", monto=item.monto, tipo="Gasto", categoria="Otros", usuario_id=uid))
    elif tipo == "fijo":
        item = GastoFijo.query.get(id)
        if item and not item.pagado_este_mes:
            item.pagado_este_mes = True
            db.session.add(Movimiento(descripcion=f"PAGO FIJO: {item.descripcion}", monto=item.monto, tipo="Gasto", categoria="Servicios", usuario_id=uid))
    db.session.commit()
    return redirect(url_for('seccion', tipo=tipo))

@app.route("/seccion/<tipo>", methods=["GET", "POST"])
def seccion(tipo):
    if 'u_id' not in session: return redirect(url_for('login'))
    uid = session['u_id']
    cat_def = request.args.get('cat', '')
    tipo_def = request.args.get('t', 'Gasto')

    if request.method == "POST":
        desc = request.form.get("descripcion"); monto = float(request.form.get("monto"))
        if tipo == "movimiento":
            db.session.add(Movimiento(descripcion=desc, monto=monto, tipo=request.form.get("tipo"), categoria=request.form.get("categoria"), usuario_id=uid))
        elif tipo == "pendiente":
            db.session.add(Pendiente(descripcion=desc, monto=monto, usuario_id=uid))
        elif tipo == "fijo":
            db.session.add(GastoFijo(descripcion=desc, monto=monto, dia_cobro=int(request.form.get("dia")), usuario_id=uid))
        db.session.commit()
        return redirect(url_for('seccion', tipo=tipo))
    
    datos = Movimiento.query.filter_by(usuario_id=uid).order_by(Movimiento.fecha.desc()).all() if tipo == "movimiento" else \
            Pendiente.query.filter_by(usuario_id=uid).all() if tipo == "pendiente" else \
            GastoFijo.query.filter_by(usuario_id=uid).all()
            
    return render_template("seccion.html", tipo=tipo, datos=datos, cat_def=cat_def, tipo_def=tipo_def)

@app.route("/borrar/<tipo>/<int:id>")
def borrar(tipo, id):
    if 'u_id' not in session: return redirect(url_for('login'))
    obj = Movimiento.query.get(id) if tipo=="movimiento" else Pendiente.query.get(id) if tipo=="pendiente" else GastoFijo.query.get(id)
    if obj and obj.usuario_id == session['u_id']:
        db.session.delete(obj); db.session.commit()
    return redirect(url_for('seccion', tipo=tipo))

@app.route("/actualizar_meta", methods=["POST"])
def actualizar_meta():
    if 'u_id' not in session: return redirect(url_for('login'))
    nueva_meta = request.form.get("nueva_meta")
    if nueva_meta:
        user = Usuario.query.get(session['u_id'])
        user.meta_ahorro = float(nueva_meta)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for('login'))

if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=puerto)