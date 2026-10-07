from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
import pandas as pd
from datetime import datetime
import os, time

# SELENIUM
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# =========================
# FLASK
# =========================
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///datosAtlas.db'
db = SQLAlchemy(app)

# =========================
# CONFIG
# =========================
RUTA_BASE = os.path.join(os.getcwd(), "descargas")
os.makedirs(RUTA_BASE, exist_ok=True)

driver = None

# =========================
# MODELO
# =========================
class Registro(db.Model):
    __tablename__ = 'datosAtlas'
    id = db.Column(db.Integer, primary_key=True)
    usuario = db.Column(db.String(100))
    parque = db.Column(db.String(100))
    clima = db.Column(db.String(10))
    vuelos = db.Column(db.Integer)
    fecha = db.Column(db.String(20))  # día lógico
    turno = db.Column(db.String(10))
    fecha_registro = db.Column(db.String(50))  # 🔥 clave real

    __table_args__ = (
        db.UniqueConstraint('usuario', 'parque', 'fecha_registro', name='uq_registro'),
    )

# =========================
# FUNCIONES ROBOT
# =========================
def detectar_turno():
    h = datetime.now().hour
    return "DIURNO" if 6 <= h < 18 else "NOCTURNO"

def obtener_rango_dia():
    ahora = datetime.now()
    inicio = ahora.replace(hour=0, minute=0, second=0)
    fin = ahora.replace(hour=23, minute=59, second=59)
    return inicio, fin

def iniciar_driver():
    global driver
    if driver is None:
        options = webdriver.ChromeOptions()

        prefs = {
            "download.default_directory": RUTA_BASE,
            "download.prompt_for_download": False
        }
        options.add_experimental_option("prefs", prefs)

        # modo invisible
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
    return driver

def login():
    d = iniciar_driver()
    d.get("https://login.atlasola.com.co/")
    wait = WebDriverWait(d, 30)

    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))).send_keys("860063875")
    d.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys("73+e$zOs1+O@I")
    d.find_element(By.TAG_NAME, "button").click()

    time.sleep(10)
    d.get("https://nuevo.atlasola.com.co/index.php/form")

def descargar_csv():
    d = iniciar_driver()
    wait = WebDriverWait(d, 30)

    d.get("https://nuevo.atlasola.com.co/index.php/form/2821/solution")
    time.sleep(5)

    inicio, fin = obtener_rango_dia()
    rango = f"{inicio:%Y-%m-%d %H:%M:%S} : {fin:%Y-%m-%d %H:%M:%S}"

    campo = wait.until(EC.presence_of_element_located((By.ID, "survey_solution_filters_saved_at")))
    d.execute_script("arguments[0].removeAttribute('readonly')", campo)
    campo.clear()
    campo.send_keys(rango)

    wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Filtrar')]"))).click()
    time.sleep(5)

    btn = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(.,'Exportar')]")))
    d.execute_script("arguments[0].click();", btn)

    print("📥 Descargando CSV del día completo...")

    # esperar descarga
    for _ in range(30):
        files = [f for f in os.listdir(RUTA_BASE) if f.endswith(".csv")]
        if files:
            break
        time.sleep(1)

    if not files:
        return None

    archivo = max(files, key=lambda x: os.path.getctime(os.path.join(RUTA_BASE, x)))
    return os.path.join(RUTA_BASE, archivo)

# =========================
# GUARDAR SIN DUPLICAR
# =========================
def guardar_csv(ruta):
    try:
        df = pd.read_csv(ruta, encoding='latin-1', on_bad_lines='skip')
        df.columns = df.columns.str.strip()

        nuevos = 0

        for _, row in df.iterrows():
            usuario = row.get("Usuario")
            parque = row.get("SELECCIONE PARQUE FOTOVOLTAICO")
            clima = row.get("CONDICION DEL CLIMA ES APTO?")
            fecha_registro = row.get("Created At")  # 🔥 AJUSTA SI CAMBIA

            if not fecha_registro:
                continue

            existe = Registro.query.filter_by(
                usuario=usuario,
                parque=parque,
                fecha_registro=fecha_registro
            ).first()

            if existe:
                continue

            reg = Registro(
                usuario=usuario,
                parque=parque,
                clima=clima,
                vuelos=1 if str(clima).upper() == "SI" else 0,
                fecha=datetime.now().strftime("%Y-%m-%d"),
                turno=detectar_turno(),
                fecha_registro=fecha_registro
            )

            db.session.add(reg)
            nuevos += 1

        db.session.commit()

        print(f"✅ Nuevos registros insertados: {nuevos}")

        # borrar CSV
        os.remove(ruta)

    except Exception as e:
        print("❌ Error guardando:", e)

# =========================
# JOB AUTOMÁTICO
# =========================
def job():
    print("⏳ Sincronizando Atlas (día completo)...")
    try:
        login()
        ruta = descargar_csv()

        if ruta:
            guardar_csv(ruta)

    except Exception as e:
        print("❌ Error job:", e)

scheduler = BackgroundScheduler()
scheduler.add_job(job, 'interval', hours=1)
scheduler.start()

# =========================
# RUTAS
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/consultar", methods=["POST"])
def consultar():
    fecha = request.form.get("fecha")
    turno = request.form.get("turno")

    registros = Registro.query.filter_by(fecha=fecha, turno=turno).all()

    data = [{
        "usuario": r.usuario,
        "parque": r.parque,
        "vuelos": r.vuelos
    } for r in registros]

    df = pd.DataFrame(data)

    if df.empty:
        return jsonify([])

    pivot = df.groupby("usuario").agg({
        "parque": "count",
        "vuelos": "sum"
    }).reset_index()

    pivot.rename(columns={
        "parque": "REGISTROS",
        "vuelos": "VUELOS"
    }, inplace=True)

    return jsonify(pivot.to_dict(orient="records"))

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)