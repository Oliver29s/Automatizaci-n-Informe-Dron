import time
import sqlite3
import pandas as pd
import threading
import os
from datetime import datetime, timedelta



# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# =========================
# ROBOT ATLAS
# =========================
class RobotAtlasPro:

    def __init__(self):
        base = os.path.dirname(os.path.abspath(__file__))
        self.ruta_base = os.path.join(base, "AtlasDescargas")
        os.makedirs(self.ruta_base, exist_ok=True)
        self.driver = None
        self.ultima_ejecucion = {}

        self.minimos = {
            "DIURNO": {"VUELOS": 8, "REGISTROS": 9},
            "NOCTURNO": {"VUELOS": 8, "REGISTROS": 15}
        }

    # ---------------- TURNO ----------------
    def detectar_turno(self):
        hora = datetime.now().hour
        return "DIURNO" if 6 <= hora < 18 else "NOCTURNO"

    def crear_carpeta_turno(self, turno):
        fecha = datetime.now().strftime("%Y-%m-%d")
        ruta = os.path.join(self.ruta_base, f"{fecha}_{turno}")
        os.makedirs(ruta, exist_ok=True)
        return ruta

    # ---------------- LOGIN ----------------
    def iniciar_sesion(self):
        if not self.driver:
            # Eliminar lock de webdriver_manager si existe para evitar errores
            import glob
            lock_files = glob.glob(os.path.join(os.path.expanduser("~"), ".wdm", ".wdm-lock-*"))
            for lock_file in lock_files:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass

            options = webdriver.ChromeOptions()
            prefs = {
                "download.default_directory": self.ruta_base,
                "download.prompt_for_download": False
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

        time.sleep(15)
        self.driver.get("https://nuevo.atlasola.com.co/index.php/form")

    # ---------------- DESCARGA ----------------
    def aplicar_filtro_y_descargar(self):
        # Limpiar descargas previas antes de empezar
        for f in os.listdir(self.ruta_base):
            if f.endswith(".csv") or f.endswith(".crdownload"):
                try:
                    os.remove(os.path.join(self.ruta_base, f))
                except Exception:
                    pass

        wait = WebDriverWait(self.driver, 30)
        self.driver.get("https://nuevo.atlasola.com.co/index.php/form/2821/solution")
        time.sleep(5)

        ahora = datetime.now()
        turno = self.detectar_turno()

        if turno == "DIURNO":
            # Todo el día actual
            inicio = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
            fin = ahora.replace(hour=23, minute=59, second=59, microsecond=0)
        else: # NOCTURNO
            if ahora.hour >= 18:
                # Si estamos entre las 18:00 y las 23:59 (El turno empezó hoy y termina mañana)
                inicio = ahora.replace(hour=18, minute=0, second=0, microsecond=0)
                fin = (ahora + timedelta(days=1)).replace(hour=5, minute=59, second=59, microsecond=0)
            else:
                # Si estamos entre las 00:00 y las 05:59 (El turno empezó ayer y termina hoy)
                inicio = (ahora - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
                fin = ahora.replace(hour=5, minute=59, second=59, microsecond=0)

        rango = f"{inicio:%Y-%m-%d %H:%M:%S} : {fin:%Y-%m-%d %H:%M:%S}"

        campo = wait.until(EC.presence_of_element_located((By.ID, "survey_solution_filters_saved_at")))
        self.driver.execute_script("arguments[0].removeAttribute('readonly')", campo)
        campo.clear()
        campo.send_keys(rango)

        wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Filtrar')]"))).click()
        time.sleep(8)

        boton_exportar = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//a[contains(@class,'btn-export') and contains(.,'Exportar')]")
            )
        )
        self.driver.execute_script("arguments[0].click();", boton_exportar)
        print("📥 Exportando CSV...")

        ruta = self.esperar_y_mover()
        if ruta:
            print(f"✅ Archivo descargado y guardado en: {ruta}")
            # self.procesar_csv(ruta) # Deshabilitado a petición del usuario
        else:
            raise Exception("No se pudo descargar el archivo CSV dentro del tiempo esperado.")

    # ---------------- ESPERAR Y MOVER ----------------
    def esperar_y_mover(self):
        print("⏳ Esperando a que finalice la descarga del formulario 2821...")
        archivo_csv = None
        while True:
            archivos = os.listdir(self.ruta_base)
            crdownloads = [f for f in archivos if f.endswith(".crdownload")]
            csvs = [f for f in archivos if f.endswith(".csv")]
            
            # Si hay un crdownload, sigue descargando
            if crdownloads:
                time.sleep(2)
                continue
            
            # Si hay un CSV y no hay descargas activas, terminamos
            if csvs and not crdownloads:
                archivo_csv = csvs[0]
                break
                
            time.sleep(2)

        origen_csv = os.path.join(self.ruta_base, archivo_csv)
        nuevo_csv = os.path.join(self.ruta_base, "formulario_2821.csv")
        
        # Renombrar para que el código lo identifique y no se confunda
        try:
            if os.path.exists(nuevo_csv):
                os.remove(nuevo_csv)
            os.rename(origen_csv, nuevo_csv)
        except Exception as e:
            print(f"⚠️ No se pudo renombrar el CSV: {e}")
            nuevo_csv = origen_csv

        try:
            print("🔄 Guardando datos en SQLite...")
            with open(origen_csv, 'r', encoding='latin-1') as f:
                primera_linea = f.readline()
            
            sep = ',' if primera_linea.count(',') > primera_linea.count(';') else ';'
            df = pd.read_csv(origen_csv, encoding='latin-1', sep=sep, on_bad_lines='skip')
            df.columns = df.columns.str.strip()
            
            db_path = os.path.join(self.ruta_base, "datosAtlas.db")
            conn = sqlite3.connect(db_path)
            
            columnas_clave = ["Usuario", "SELECCIONE PARQUE FOTOVOLTAICO", "Created At"]
            subset_cols = [col for col in columnas_clave if col in df.columns]
            subset_cols = subset_cols if subset_cols else None
            
            try:
                # Intentar leer datos existentes para limpiar posibles duplicados guardados antes
                df_existente = pd.read_sql("SELECT * FROM registros_dron", conn)
                df_final = pd.concat([df_existente, df]).drop_duplicates(subset=subset_cols, keep='last')
            except Exception:
                # Si la tabla no existe, usar el dataframe actual sin duplicados
                df_final = df.drop_duplicates(subset=subset_cols, keep='last')
                
            df_final.to_sql("registros_dron", conn, if_exists="replace", index=False)
            conn.close()
            os.remove(origen_csv)
            print(f"✅ Datos guardados en base de datos SQLite: {db_path}")
            return db_path
        except Exception as e:
            print(f"❌ Error al guardar en SQLite: {e}")
            return None

    # ---------------- PROCESAR CSV ----------------
    def procesar_csv(self, ruta):
        try:
            with open(ruta, 'r', encoding='latin-1') as f:
                primera_linea = f.readline()

            sep = ',' if primera_linea.count(',') > primera_linea.count(';') else ';'

            df = pd.read_csv(ruta, encoding='latin-1', sep=sep, on_bad_lines='skip')
            df.columns = df.columns.str.strip()

            df = df.rename(columns={
                "Usuario": "USUARIO",
                "SELECCIONE PARQUE FOTOVOLTAICO": "PARQUE",
                "CONDICION DEL CLIMA ES APTO?": "CLIMA"
            })

            df["VUELOS REALIZADOS"] = df["CLIMA"].apply(lambda x: 1 if str(x).upper() == "SI" else 0)

            resumen = df.groupby("USUARIO").agg({
                "PARQUE": "count",
                "VUELOS REALIZADOS": "sum"
            }).rename(columns={"PARQUE": "REGISTROS"})

            turno = self.detectar_turno()

            resumen["VUELOS PROG"] = self.minimos[turno]["VUELOS"]
            resumen["REG PROG"] = self.minimos[turno]["REGISTROS"]

            resumen["% VUELOS"] = resumen["VUELOS REALIZADOS"] / resumen["VUELOS PROG"] * 100
            resumen["% REGISTROS"] = resumen["REGISTROS"] / resumen["REG PROG"] * 100

            salida = ruta.replace(".csv", "_procesado.xlsx")
            resumen.to_excel(salida)

            print(f"📊 Procesado: {salida}")

        except Exception as e:
            print(f"❌ Error procesando CSV: {e}")

    # ---------------- AUTOMÁTICO POR HORAS ----------------
    def ejecutar_automatico(self):
        while True:
            ahora = datetime.now()
            hora_actual = ahora.strftime("%H:%M")
            fecha = ahora.strftime("%Y-%m-%d")

            # 🌙 NOCTURNO → 05:45 AM
            if "05:45" <= hora_actual < "05:47":
                clave = f"{fecha}_NOCTURNO"

                if self.ultima_ejecucion.get("nocturno") != clave:
                    print("🌙 Ejecutando cierre turno NOCTURNO (05:45 AM)")
                    try:
                        self.iniciar_sesion()
                        self.aplicar_filtro_y_descargar()
                        self.ultima_ejecucion["nocturno"] = clave
                    except Exception as e:
                        print(f"❌ Error nocturno: {e}")

            # ☀️ DIURNO → 17:45 PM
            if "17:45" <= hora_actual < "17:47":
                clave = f"{fecha}_DIURNO"

                if self.ultima_ejecucion.get("diurno") != clave:
                    print("☀️ Ejecutando cierre turno DIURNO (17:45 PM)")
                    try:
                        self.iniciar_sesion()
                        self.aplicar_filtro_y_descargar()
                        self.ultima_ejecucion["diurno"] = clave
                    except Exception as e:
                        print(f"❌ Error diurno: {e}")

            time.sleep(30)



# =========================
# MAIN
# =========================
if __name__ == "__main__":
    print("Iniciando Robot Atlas PRO en modo consola (Ejecución cada 1 hora)...")
    bot = RobotAtlasPro()
    
    while True:
        try:
            print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Iniciando proceso de descarga...")
            bot.iniciar_sesion()
            bot.aplicar_filtro_y_descargar()
            print("✅ Proceso finalizado exitosamente.")
        except Exception as e:
            print(f"❌ Error durante la ejecución: {e}")
        finally:
            if bot.driver:
                try:
                    bot.driver.quit()
                except Exception:
                    pass
                bot.driver = None
                
        print("⏳ Esperando 1 hora para la próxima ejecución...")
        time.sleep(3600)