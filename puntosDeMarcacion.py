import time
import os
import json
import glob
import sqlite3
import threading
import pandas as pd
from datetime import datetime
from calendar import monthrange

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


class RobotAtlasSQLiteAsincrono:

    def __init__(self):
        base = os.path.dirname(os.path.abspath(__file__))
        self.ruta_base = os.path.join(base, "AtlasDescargas")
        os.makedirs(self.ruta_base, exist_ok=True)
        
        self.db_path = os.path.join(self.ruta_base, "atlas_database.db")
        self.progreso_path = os.path.join(self.ruta_base, "progreso.json")
        self.driver = None
        self.form_actual = None
        
        # Banderas de control de hilos
        self.stop_worker = False
        
        # Lock para evitar concurrencia de escrituras en SQLite
        self.db_lock = threading.Lock()

        # Inicializar estructura en SQLite
        self.inicializar_db()

    # ---------------- BASE DE DATOS SQLITE ----------------
    def inicializar_db(self):
        """Crea la estructura de tablas e índices en SQLite."""
        with self.db_lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Tabla de control de cargas por archivo
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archivos_procesados (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nombre_archivo TEXT UNIQUE,
                    fecha_procesado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()

    def guardar_df_en_sqlite(self, df, nombre_archivo):
        """Inserta el DataFrame en SQLite usando un Lock thread-safe."""
        if df.empty:
            return 0

        with self.db_lock:
            conn = sqlite3.connect(self.db_path)
            
            # pandas.to_sql crea y ajusta la tabla automáticamente según las columnas del CSV
            df.to_sql("datos_atlas_raw", conn, if_exists="append", index=False)
            
            # Registrar el archivo como cargado para evitar duplicados
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO archivos_procesados (nombre_archivo) VALUES (?)",
                (nombre_archivo,)
            )
            
            registros = len(df)
            conn.commit()
            conn.close()
            return registros

    # ---------------- CONTROL DE PROGRESO ----------------
    def cargar_progreso(self):
        if os.path.exists(self.progreso_path):
            try:
                with open(self.progreso_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def registrar_progreso(self, clave):
        progreso = self.cargar_progreso()
        progreso[clave] = True
        with open(self.progreso_path, "w", encoding="utf-8") as f:
            json.dump(progreso, f, indent=4)

    # ---------------- HILO SECUNDARIO (CONSUMIDOR SQLITE) ----------------
    def worker_carga_sqlite(self):
        """Proceso asíncrono que corre en segundo plano escaneando descargas."""
        print("⚙️ [HILO SQLITE] Iniciado monitoreo constante de la carpeta de descargas...")
        
        while not self.stop_worker:
            self._procesar_csvs_pendientes()
            time.sleep(2)  # Frecuencia de escaneo
            
        # Barrido final antes de apagar el hilo
        print("⚙️ [HILO SQLITE] Realizando barrido final de archivos...")
        self._procesar_csvs_pendientes()

    def _procesar_csvs_pendientes(self):
        """Busca CSVs liberados por el navegador/antivirus y los guarda en SQLite."""
        try:
            archivos = os.listdir(self.ruta_base)
            csvs = [f for f in archivos if f.endswith(".csv") and not f.startswith("FORM")]

            for csv_file in csvs:
                ruta_csv = os.path.join(self.ruta_base, csv_file)

                # Verificar si aún se está escribiendo / liberado por el antivirus
                try:
                    tamano1 = os.path.getsize(ruta_csv)
                    time.sleep(0.5)
                    tamano2 = os.path.getsize(ruta_csv)

                    if tamano1 != tamano2 or tamano1 == 0:
                        continue  # Archivo en proceso de escritura
                except Exception:
                    continue

                # Cargar y transformar
                try:
                    with open(ruta_csv, 'r', encoding='latin-1') as f:
                        primera_linea = f.readline()

                    if not primera_linea:
                        os.remove(ruta_csv)
                        continue

                    sep = ',' if primera_linea.count(',') > primera_linea.count(';') else ';'
                    df = pd.read_csv(ruta_csv, encoding='latin-1', sep=sep, on_bad_lines='skip')

                    if df.empty:
                        os.remove(ruta_csv)
                        continue

                    df.columns = df.columns.str.strip()

                    # Mapeo y limpieza de campos
                    if "Seleccione Puesto a Revisar" in df.columns:
                        df.rename(columns={"Seleccione Puesto a Revisar": "PUNTO DE MARCACION"}, inplace=True)
                    elif "LEER QR" in df.columns:
                        df.rename(columns={"LEER QR": "PUNTO DE MARCACION"}, inplace=True)

                    if "Fecha de captura" in df.columns:
                        df.rename(columns={"Fecha de captura": "FECHA DE CAPTURA"}, inplace=True)

                    def extraer_punto(valor):
                        if pd.isna(valor): return valor
                        try:
                            obj = json.loads(str(valor))
                            if "data" in obj and isinstance(obj["data"], str):
                                obj = json.loads(obj["data"])
                            return obj.get("nombre", valor)
                        except Exception:
                            return valor

                    if "PUNTO DE MARCACION" in df.columns:
                        df["PUNTO DE MARCACION"] = df["PUNTO DE MARCACION"].apply(extraer_punto)

                    df = df.fillna("")

                    # Inserción asíncrona a SQLite
                    num_filas = self.guardar_df_en_sqlite(df, nombre_archivo=csv_file)
                    print(f"✅ [SQLITE AUTO] {num_filas} filas insertadas desde: {csv_file}")

                    # Eliminar el CSV para liberar espacio y no reprocesar
                    os.remove(ruta_csv)

                except PermissionError:
                    # Antivirus o sistema bloqueando temporalmente el archivo
                    pass
                except Exception as e:
                    print(f"⚠️ Error procesando {csv_file}: {e}")

        except Exception as e:
            print(f"⚠️ Error en ciclo del worker SQLite: {e}")

    # ---------------- NAVEGADOR Y AUTENTICACIÓN ----------------
    def iniciar_sesion(self):
        if not self.driver:
            lock_files = glob.glob(os.path.join(os.path.expanduser("~"), ".wdm", ".wdm-lock-*"))
            for lock_file in lock_files:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            options = webdriver.ChromeOptions()
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--ignore-certificate-errors")
            options.add_argument("--allow-running-insecure-content")
            
            prefs = {
                "download.default_directory": self.ruta_base,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "profile.managed_default_content_settings.images": 2
            }
            options.add_experimental_option("prefs", prefs)

            self.driver = webdriver.Chrome(
                service=Service(ChromeDriverManager().install()),
                options=options
            )

        self.driver.get("https://login.atlasola.com.co/")
        wait = WebDriverWait(self.driver, 30)

        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))).send_keys("860063875")
        self.driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys("73+e$zOs1+O@I")
        self.driver.find_element(By.TAG_NAME, "button").click()

        time.sleep(4)
        self.driver.get("https://nuevo.atlasola.com.co/index.php/form")

    def cerrar_sesion(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ---------------- DISPARADOR DE DESCARGAS ----------------
    def disparar_exportacion(self, form_id, fecha_inicio_dt, fecha_fin_dt):
        """Envía la solicitud de exportación a Chrome sin esperar la descarga."""
        inicio_dt = fecha_inicio_dt.replace(hour=0, minute=0, second=0)
        fin_dt = fecha_fin_dt.replace(hour=23, minute=59, second=59)
        rango = f"{inicio_dt:%Y-%m-%d %H:%M:%S} : {fin_dt:%Y-%m-%d %H:%M:%S}"

        try:
            wait = WebDriverWait(self.driver, 30)
            url_deseada = f"https://nuevo.atlasola.com.co/index.php/form/{form_id}/solution"

            if self.driver.current_url != url_deseada or self.form_actual != form_id:
                self.driver.get(url_deseada)
                self.form_actual = form_id
                time.sleep(2)

            campo = wait.until(EC.presence_of_element_located((By.ID, "survey_solution_filters_saved_at")))
            
            # Inyección JS
            self.driver.execute_script("""
                arguments[0].removeAttribute('readonly');
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('change'));
                arguments[0].dispatchEvent(new Event('input'));
            """, campo, rango)

            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Filtrar')]"))).click()
            time.sleep(3)

            boton_exportar = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//a[contains(@class,'btn-export') and contains(.,'Exportar')]")
                )
            )
            # Clic inmediato
            self.driver.execute_script("arguments[0].click();", boton_exportar)
            print(f"🚀 [DISPARADO] Formulario {form_id} | Rango: {rango}")
            time.sleep(2)
            return True

        except Exception as e:
            print(f"⚠️ Error disparando descarga (Form {form_id}): {e}")
            return False

    # ---------------- GENERADOR DE RANGOS ----------------
    def generar_rangos(self, fecha_inicio_str, modo="quincenal"):
        fecha_inicio = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
        fecha_hoy = datetime.now()
        rangos = []

        cursor = fecha_inicio
        while cursor <= fecha_hoy:
            year = cursor.year
            month = cursor.month
            ultimo_dia_mes = monthrange(year, month)[1]

            if modo == "quincenal":
                q1_inicio = datetime(year, month, 1)
                q1_fin = datetime(year, month, 15)
                if q1_fin >= fecha_inicio and q1_inicio <= fecha_hoy:
                    fin_real = min(q1_fin, fecha_hoy)
                    rangos.append((q1_inicio, fin_real, f"{q1_inicio:%Y%m%d}_Q1"))

                q2_inicio = datetime(year, month, 16)
                q2_fin = datetime(year, month, ultimo_dia_mes)
                if q2_fin >= fecha_inicio and q2_inicio <= fecha_hoy:
                    fin_real = min(q2_fin, fecha_hoy)
                    rangos.append((q2_inicio, fin_real, f"{q2_inicio:%Y%m%d}_Q2"))

                cursor = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

            elif modo == "mensual":
                m_inicio = datetime(year, month, 1)
                m_fin = datetime(year, month, ultimo_dia_mes)
                if m_fin >= fecha_inicio and m_inicio <= fecha_hoy:
                    fin_real = min(m_fin, fecha_hoy)
                    rangos.append((m_inicio, fin_real, f"{m_inicio:%Y%m}"))

                cursor = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        return rangos

    # ---------------- EJECUCIÓN ----------------
    def ejecutar(self, fecha_inicio_str="2026-06-01", modo="quincenal"):
        rangos = self.generar_rangos(fecha_inicio_str, modo=modo)
        print(f"⚡ Iniciando proceso ASÍNCRONO con SQLite desde {fecha_inicio_str} en modo [{modo.upper()}]...")

        # 1. Iniciar el hilo de carga en segundo plano
        self.stop_worker = False
        thread_sqlite = threading.Thread(target=self.worker_carga_sqlite, daemon=True)
        thread_sqlite.start()

        try:
            # 2. Iniciar sesión y realizar disparos masivos en Chrome
            self.iniciar_sesion()

            for inicio_dt, fin_dt, etiqueta in rangos:
                progreso = self.cargar_progreso()

                for form_id in ["10", "03"]:
                    clave_progreso = f"FORM_{form_id}_{etiqueta}"

                    if progreso.get(clave_progreso):
                        print(f"⏩ {clave_progreso} ya fue exportado previamente. Omitiendo...")
                        continue

                    exito = self.disparar_exportacion(form_id, inicio_dt, fin_dt)
                    if exito:
                        self.registrar_progreso(clave_progreso)

            print("\n🏁 [DISPAROS FINALIZADOS] Esperando a que el navegador complete las descargas restantes...")
            
            # 3. Monitorear hasta que no haya descargas activas en Chrome (.crdownload / .tmp / .csv)
            espera_maxima = 300  # 5 minutos máximo
            tiempo = 0
            while tiempo < espera_maxima:
                archivos = os.listdir(self.ruta_base)
                pendientes = [f for f in archivos if f.endswith(".crdownload") or f.endswith(".tmp") or (f.endswith(".csv") and not f.startswith("FORM"))]
                
                if not pendientes:
                    print("✨ ¡Proceso completado! Todos los archivos fueron descargados e insertados en SQLite.")
                    break
                    
                time.sleep(5)
                tiempo += 5

        except Exception as e:
            print(f"❌ Error en el proceso principal: {e}")
        finally:
            self.cerrar_sesion()
            # Apagar el hilo de SQLite
            self.stop_worker = True
            thread_sqlite.join()


if __name__ == "__main__":
    bot = RobotAtlasSQLiteAsincrono()
    bot.ejecutar(fecha_inicio_str="2026-06-01", modo="mensual")