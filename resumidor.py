"""
PASO 3 - El LLM (Gemini) resume en español los resultados de openFDA.

Flujo completo:
    pregunta -> traductor (Gemini) -> consulta a openFDA -> limpieza de nombres
             -> traducción de categorías (diccionario fijo) -> total real de informes
             -> resumen (Gemini) -> comprobación de que las cifras del resumen son reales

Ejecutar con:  python resumidor.py
"""

import os
import re
import time

from google import genai
from google.genai import errors, types

from explorar_api import consultar_api, mostrar_ranking
from traductor import MAX_REINTENTOS_SERVIDOR, MODELO, ConsultaFDA, traducir_pregunta

MAX_INTENTOS_RESUMEN = 2  # correcciones si el resumen trae cifras dudosas
CAMPO_FABRICANTE = "device.manufacturer_d_name.exact"
CAMPO_MARCA = "device.brand_name.exact"
CAMPOS_SIN_TRADUCIR = {CAMPO_FABRICANTE, CAMPO_MARCA}  # nombres propios: no se traducen
FILAS_A_DESCARGAR = 100  # fabricantes: pedimos más filas y recortamos tras limpiar

# Terminaciones legales que no forman parte del nombre de la empresa
SUFIJOS_LEGALES = {"INC", "LLC", "LTD", "CORP", "CO", "GMBH", "SA", "AG"}

# Diccionario fijo inglés -> español de las categorías más comunes de MAUDE.
# Las que no estén aquí se muestran en inglés.
TRADUCCIONES = {
    "Malfunction": "Fallo de funcionamiento",
    "Injury": "Lesión",
    "Death": "Muerte",
    "Other": "Otro",
    "High Test Results": "Resultados de prueba altos",
    "Low Test Results": "Resultados de prueba bajos",
    "Non Reproducible Results": "Resultados no reproducibles",
    "False Positive Result": "Resultado falso positivo",
    "False Negative Result": "Resultado falso negativo",
    "Incorrect, Inadequate or Imprecise Result or Readings": "Resultado o lecturas incorrectos, inadecuados o imprecisos",
    "Incorrect Measurement": "Medición incorrecta",
    "Low Readings": "Lecturas bajas",
    "Computer Software Problem": "Problema de software",
    "Computer Operating System Problem": "Problema del sistema operativo",
    "Communication or Transmission Problem": "Problema de comunicación o transmisión",
    "Wireless Communication Problem": "Problema de comunicación inalámbrica",
    "Unintended Application Program Shut Down": "Cierre no intencionado del programa",
    "Inappropriate or Unexpected Reset": "Reinicio inapropiado o inesperado",
    "Output Problem": "Problema de salida",
    "Display or Visual Feedback Problem": "Problema de pantalla o indicación visual",
    "No Display/Image": "Sin pantalla o imagen",
    "Key or Button Unresponsive/not Working": "Tecla o botón que no responde",
    "Mechanical Problem": "Problema mecánico",
    "Suction Problem": "Problema de aspiración",
    "Leak/Splash": "Fuga o salpicadura",
    "Fluid/Blood Leak": "Fuga de fluido o sangre",
    "Obstruction of Flow": "Obstrucción del flujo",
    "Smoking": "Emisión de humo",
    "Crack": "Grieta",
    "Break": "Rotura",
    "Material Deformation": "Deformación del material",
    "Detachment of Device or Device Component": "Desprendimiento del dispositivo o de un componente",
    "Contamination": "Contaminación",
    "Battery Problem": "Problema de batería",
    "Charging Problem": "Problema de carga",
    "Failure to Charge": "Fallo de carga",
    "Power Problem": "Problema de alimentación eléctrica",
    "Electrical /Electronic Property Problem": "Problema de propiedades eléctricas o electrónicas",
    "Use of Device Problem": "Problema de uso del dispositivo",
    "Adverse Event Without Identified Device or Use Problem": "Evento adverso sin problema identificado del dispositivo o de su uso",
    "No Apparent Adverse Event": "Sin evento adverso aparente",
    "Insufficient Device Problem Information": "Información insuficiente sobre el problema del dispositivo",
    "Appropriate Device Problem Term/Code Not Available": "Término o código de problema adecuado no disponible",
}
# En minúsculas, para encontrar el término aunque cambien las mayúsculas
TRADUCCIONES_MINUSCULAS = {ingles.lower(): espanol for ingles, espanol in TRADUCCIONES.items()}

PROMPT_RESUMEN = """
Eres un analista que explica datos de la base MAUDE de la FDA (incidencias de dispositivos médicos)
a un ingeniero biomédico. Recibirás una pregunta en español, la consulta usada, un ranking con cifras
y el TOTAL real de informes que cumplen la búsqueda.

Escribe un resumen en español de 3 a 4 frases cortas, en texto plano (sin viñetas, sin negritas, sin títulos).
Menciona con su cifra solo las 3 primeras categorías del ranking; no enumeres el resto.

Reglas estrictas:
- Usa SOLO las cifras que aparecen en los datos. No inventes, no redondees y no calcules
  porcentajes, sumas ni proporciones.
- Si das un total, usa únicamente el "TOTAL real de informes" que se te indica. NUNCA sumes las filas
  del ranking ni presentes esa suma como total.
- Un mismo informe puede aparecer en varias categorías (por ejemplo, tener varios problemas),
  así que las cifras de las categorías no se pueden sumar entre sí.
- El ranking es un "top": son las categorías más frecuentes, no necesariamente todas.
- Cada cifra es un número de INFORMES de MAUDE, no de incidentes confirmados ni una tasa de fallos.
  Los informes pueden estar duplicados o incompletos y no prueban que el equipo causara el problema.
- Si comparas fabricantes, modelos o marcas, aclara que tener más informes NO significa que sea peor:
  depende de cuántos equipos haya instalados en el mercado, y MAUDE no contiene esa cifra.
- Si hay pocos datos o los resultados no permiten concluir algo, dilo claramente.
- Menciona el periodo de tiempo si aparece en la consulta.
- Escribe las cifras en formato español, con punto para los miles (1.219), tal como aparecen en los datos.
  Los años van sin separador (2025).
"""


def formatear_miles(numero: int) -> str:
    """1219 -> '1.219' (formato español)."""
    return f"{numero:,}".replace(",", ".")


def formatear_cifras_en_texto(texto: str, anios: set[int]) -> str:
    """Pone puntos de miles a las cifras sueltas de 4+ dígitos (2094 -> 2.094), salvo los años."""

    def reemplazo(coincidencia: re.Match) -> str:
        numero = int(coincidencia.group(0))
        return coincidencia.group(0) if numero in anios else formatear_miles(numero)

    # \b\d{4,}\b solo atrapa números pegados (2094), no los ya formateados (2.094)
    return re.sub(r"\b\d{4,}\b", reemplazo, texto)


def limpiar_nombre_fabricante(nombre: str) -> str:
    """'BECKMAN COULTER, INC.' y 'Beckman Coulter Inc' -> 'BECKMAN COULTER'."""
    texto = re.sub(r"[.,]", " ", nombre.upper())
    palabras = texto.split()
    # Quitamos INC, LLC... del final, pero sin dejar el nombre vacío
    while len(palabras) > 1 and palabras[-1] in SUFIJOS_LEGALES:
        palabras.pop()
    return " ".join(palabras)


def limpiar_resultados(filas: list[dict], campo_count: str) -> list[dict]:
    """Une las variantes de un mismo fabricante y suma sus cifras. Otros campos no se tocan."""
    if campo_count != CAMPO_FABRICANTE:
        return filas

    acumulado: dict[str, int] = {}
    for fila in filas:
        nombre = limpiar_nombre_fabricante(fila["term"])
        acumulado[nombre] = acumulado.get(nombre, 0) + fila["count"]

    unidas = [{"term": nombre, "count": total} for nombre, total in acumulado.items()]
    return sorted(unidas, key=lambda fila: fila["count"], reverse=True)


def traducir_categorias(filas: list[dict], campo_count: str) -> list[dict]:
    """Traduce al español los nombres que estén en el diccionario; el resto queda en inglés."""
    if campo_count in CAMPOS_SIN_TRADUCIR:
        return filas

    traducidas = []
    for fila in filas:
        espanol = TRADUCCIONES_MINUSCULAS.get(fila["term"].strip().lower(), fila["term"])
        traducidas.append({"term": espanol, "count": fila["count"]})
    return traducidas


def consultar_y_limpiar(consulta: ConsultaFDA) -> list[dict]:
    """Consulta openFDA y devuelve el ranking ya limpio y recortado a 'limit' filas."""
    # Fabricantes: pedimos muchas filas para sumar también las variantes de un
    # mismo nombre que queden más abajo.
    es_fabricante = consulta.count == CAMPO_FABRICANTE
    limite_api = FILAS_A_DESCARGAR if es_fabricante else consulta.limit

    datos = consultar_api({"search": consulta.search, "count": consulta.count, "limit": limite_api})
    if not datos:
        return []

    filas = limpiar_resultados(datos["results"], consulta.count)
    return filas[: consulta.limit]


def contar_total(consulta: ConsultaFDA) -> int | None:
    """Total REAL de informes que cumplen la búsqueda (consulta sin 'count')."""
    datos = consultar_api({"search": consulta.search, "limit": 1})
    if not datos:
        return None
    return datos["meta"]["results"]["total"]


def numeros_en_texto(texto: str) -> set[int]:
    """Extrae los números de un texto. '1.219', '1,219' y '1219' valen todos 1219."""
    encontrados = re.findall(r"\d+(?:[.,]\d+)*", texto)
    return {int(re.sub(r"[.,]", "", numero)) for numero in encontrados}


def anios_de_la_busqueda(consulta: ConsultaFDA) -> set[int]:
    """Años que aparecen en las fechas de la búsqueda (20250101 -> 2025)."""
    return {int(fecha[:4]) for fecha in re.findall(r"\d{8}", consulta.search)}


def numeros_permitidos(consulta: ConsultaFDA, filas: list[dict], total: int | None) -> set[int]:
    """Cifras que el resumen puede mencionar legítimamente."""
    permitidos = {fila["count"] for fila in filas}
    if total is not None:
        permitidos.add(total)  # el total REAL; la suma de las filas NO está permitida

    permitidos |= anios_de_la_busqueda(consulta)
    permitidos |= {int(fecha) for fecha in re.findall(r"\d{8}", consulta.search)}

    # Posiciones del ranking: "los 3 primeros", "el segundo"...
    permitidos |= set(range(1, len(filas) + 1))
    return permitidos


def pedir_texto_a_gemini(cliente: genai.Client, mensaje: str) -> str:
    """Llama a Gemini y devuelve texto libre. Reintenta si el servidor está saturado."""
    for intento in range(1, MAX_REINTENTOS_SERVIDOR + 1):
        try:
            respuesta = cliente.models.generate_content(
                model=MODELO,
                contents=mensaje,
                config=types.GenerateContentConfig(
                    system_instruction=PROMPT_RESUMEN,
                    temperature=0.2,  # algo de variación en la redacción, no en las cifras
                ),
            )
            return (respuesta.text or "").strip()
        except errors.ServerError as error:
            if intento == MAX_REINTENTOS_SERVIDOR:
                raise
            espera = 2**intento
            print(f"  (Gemini saturado [{error.code}]; reintento {intento} en {espera} s)")
            time.sleep(espera)


def resumir(
    pregunta: str, consulta: ConsultaFDA, filas: list[dict], total: int | None
) -> tuple[str, list[int]]:
    """Devuelve (resumen, cifras_dudosas). Si 'cifras_dudosas' está vacía, todo cuadra con los datos."""
    cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    # Cifras ya en formato español: el LLM las copia tal cual
    lineas_datos = "\n".join(f"- {fila['term']}: {formatear_miles(fila['count'])}" for fila in filas)
    linea_total = (
        f"TOTAL real de informes que cumplen la búsqueda: {formatear_miles(total)}"
        if total is not None
        else "TOTAL real de informes: no disponible (no menciones ningún total)"
    )
    mensaje_base = (
        f"Pregunta del usuario: {pregunta}\n"
        f"Consulta usada: {consulta.search}\n"
        f"Campo contado: {consulta.count}\n\n"
        f"Datos (número de informes por categoría):\n{lineas_datos}\n\n"
        f"{linea_total}"
    )

    permitidos = numeros_permitidos(consulta, filas, total)
    anios = anios_de_la_busqueda(consulta)
    mensaje = mensaje_base

    for _ in range(MAX_INTENTOS_RESUMEN):
        resumen = pedir_texto_a_gemini(cliente, mensaje)
        dudosas = sorted(numeros_en_texto(resumen) - permitidos)
        if not dudosas:
            return formatear_cifras_en_texto(resumen, anios), []

        print(f"  (cifras que no están en los datos: {dudosas}; pido corregir)")
        mensaje = (
            f"{mensaje_base}\n\nTu resumen anterior fue rechazado porque contiene cifras "
            f"que no están en los datos: {dudosas}.\nResumen rechazado: {resumen}\n"
            "Reescríbelo usando únicamente las cifras de los datos."
        )

    return formatear_cifras_en_texto(resumen, anios), dudosas


def main() -> None:
    pregunta = input("Pregunta: ").strip()

    print("\nTraduciendo con Gemini...")
    consulta = traducir_pregunta(pregunta)
    print(f"  search = {consulta.search}")
    print(f"  count  = {consulta.count}")

    print("\nConsultando openFDA...")
    filas = consultar_y_limpiar(consulta)
    if not filas:
        print("No hay resultados para esta pregunta.")
        return
    filas = traducir_categorias(filas, consulta.count)
    total = contar_total(consulta)
    mostrar_ranking("Datos", filas)
    if total is not None:
        print(f"\nTotal real de informes: {formatear_miles(total)}")

    print("\nResumiendo con Gemini...")
    resumen, dudosas = resumir(pregunta, consulta, filas, total)
    print(f"\n{resumen}")
    if dudosas:
        print(f"\nAVISO: estas cifras del resumen no aparecen en los datos: {dudosas}")


if __name__ == "__main__":
    main()
