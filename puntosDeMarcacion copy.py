import time
import os
import json
import glob
import pandas as pd
from datetime import datetime, timedelta
from openpyxl import Workbook, load_workbook

# Selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

class RobotAtlasHistorico:

    def __init__(self):
        base = os.path.dirname(os.path.abspath(__file__))
        self.ruta_base = os.path.join(base, "AtlasDescargas")
        os.makedirs(self.ruta_base, exist_ok=True)
        
        self.excel_path = os.path.join(self.ruta_base, "datosAtlas.xlsx")
        self.progreso_path = os.path.join(self.ruta_base, "progreso.json")
        self.driver = None
        self.form_actual = None

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

    # ---------------- LOGIN (TIEMPOS ORIGINALES) ----------------
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
            
            prefs = {
                "download.default_directory": self.ruta_base,
                "download.prompt_for_download": False,
                "profile.managed_default_content_settings.images": 2  # No cargar imágenes
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

        time.sleep(5)
        self.driver.get("https://nuevo.atlasola.com.co/index.php/form")

    def cerrar_sesion(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None
            self.form_actual = None

    # ---------------- DESCARGA DIARIA EN LA MISMA PESTAÑA ----------------
    def aplicar_filtro_y_descargar(self, form_id, fecha_dt, etiqueta_dia, intentos_max=3):
        inicio_dt = fecha_dt.replace(hour=0, minute=0, second=0)
        fin_dt = fecha_dt.replace(hour=23, minute=59, second=59)
        rango = f"{inicio_dt:%Y-%m-%d %H:%M:%S} : {fin_dt:%Y-%m-%d %H:%M:%S}"

        for intento in range(1, intentos_max + 1):
            try:
                # Limpiar temporales
                for f in os.listdir(self.ruta_base):
                    if f.endswith(".crdownload") or (f.endswith(".csv") and not f.startswith("FORM")):
                        try:
                            os.remove(os.path.join(self.ruta_base, f))
                        except Exception:
                            pass

                wait = WebDriverWait(self.driver, 30)
                url_deseada = f"https://nuevo.atlasola.com.co/index.php/form/{form_id}/solution"

                # Carga la URL solo si cambia de formulario o la sesión se reinició
                if self.driver.current_url != url_deseada or self.form_actual != form_id:
                    self.driver.get(url_deseada)
                    self.form_actual = form_id
                    time.sleep(2)

                campo = wait.until(EC.presence_of_element_located((By.ID, "survey_solution_filters_saved_at")))
                
                # Inyección JS en la misma vista abierta
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
                self.driver.execute_script("arguments[0].click();", boton_exportar)

                nombre_personalizado = f"FORM{form_id}_{etiqueta_dia}.csv"
                if self.esperar_y_mover(nombre_personalizado):
                    return True  # Descarga exitosa

            except Exception as e:
                print(f"⚠️ Reintentando {etiqueta_dia} Form {form_id} (Intento {intento}/{intentos_max}): {e}")
                time.sleep(3)
                # En caso de fallo crítico de sesión, reinicia la sesión en la misma ventana
                self.cerrar_sesion()
                self.iniciar_sesion()

        return False

    # ---------------- ESPERAR DESCARGA ----------------
    def esperar_y_mover(self, nombre_personalizado):
        archivo_csv = None

        while True:
            archivos = os.listdir(self.ruta_base)
            crdownloads = [f for f in archivos if f.endswith(".crdownload")]
            csvs = [f for f in archivos if f.endswith(".csv") and not f.startswith("FORM")]

            if crdownloads:
                time.sleep(1)
                continue

            if csvs and not crdownloads:
                archivo_csv = csvs[0]
                break

            time.sleep(1)

        origen_csv = os.path.join(self.ruta_base, archivo_csv)
        nuevo_csv = os.path.join(self.ruta_base, nombre_personalizado)

        try:
            if os.path.exists(nuevo_csv):
                os.remove(nuevo_csv)
            os.rename(origen_csv, nuevo_csv)
        except Exception as e:
            print(f"⚠️ Error al renombrar: {e}")
            nuevo_csv = origen_csv

        self.guardar_en_excel_rapido(nuevo_csv)
        return True

    # ---------------- ESCRITURA RÁPIDA EN EXCEL ----------------
    def guardar_en_excel_rapido(self, origen_csv):
        try:
            with open(origen_csv, 'r', encoding='latin-1') as f:
                primera_linea = f.readline()

            if not primera_linea:
                print("⚠️ Archivo vacío.")
                os.remove(origen_csv)
                return

            sep = ',' if primera_linea.count(',') > primera_linea.count(';') else ';'
            df = pd.read_csv(origen_csv, encoding='latin-1', sep=sep, on_bad_lines='skip')

            if df.empty:
                print("⚠️ Archivo sin registros.")
                os.remove(origen_csv)
                return

            df.columns = df.columns.str.strip()

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

            # Guardar eficientemente con openpyxl en el mismo libro de Excel
            if not os.path.exists(self.excel_path):
                wb = Workbook()
                ws = wb.active
                ws.title = "DatosAtlas"
                ws.append(list(df.columns))
            else:
                wb = load_workbook(self.excel_path)
                ws = wb["DatosAtlas"] if "DatosAtlas" in wb.sheetnames else wb.active

            for row in df.itertuples(index=False, name=None):
                ws.append(list(row))

            wb.save(self.excel_path)
            wb.close()

            os.remove(origen_csv)
            print(f"📊 Datos guardados en Excel ({len(df)} filas).")

        except Exception as e:
            print(f"❌ Error al guardar en Excel: {e}")
            if os.path.exists(origen_csv):
                os.remove(origen_csv)

    # ---------------- BUCLE DÍA A DÍA ----------------
    def ejecutar_barrido_historico(self, fecha_inicio_str="2026-06-01"):
        fecha_cursor = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
        fecha_hoy = datetime.now()

        print(f"🚀 Iniciando extracción DÍA A DÍA desde {fecha_inicio_str}...")

        try:
            self.iniciar_sesion()

            while fecha_cursor <= fecha_hoy:
                dia_str = fecha_cursor.strftime("%Y-%m-%d")
                print(f"\n📅 --- PROCESANDO DÍA: {dia_str} ---")

                progreso = self.cargar_progreso()

                for form_id in ["10", "03"]:
                    clave_progreso = f"FORM_{form_id}_{dia_str}"

                    if progreso.get(clave_progreso):
                        print(f"⏩ Día {dia_str} Formulario {form_id} ya procesado. Omitiendo...")
                        continue

                    exito = self.aplicar_filtro_y_descargar(form_id, fecha_cursor, dia_str)
                    
                    if exito:
                        self.registrar_progreso(clave_progreso)
                    else:
                        print(f"❌ No se pudo descargar el día {dia_str} Formulario {form_id}.")

                fecha_cursor += timedelta(days=1)

            print("\n🎉 ¡Proceso diario completado con éxito en datosAtlas.xlsx!")

        except Exception as e:
            print(f"❌ Error en el proceso: {e}")
        finally:
            self.cerrar_sesion()


if __name__ == "__main__":
    bot = RobotAtlasHistorico()
    bot.ejecutar_barrido_historico("2026-06-01")