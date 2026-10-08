"""
PASO 2 - El LLM (Gemini) traduce una pregunta en español a parámetros de la API openFDA.

Flujo:
    pregunta en español -> Gemini -> JSON {search, count, limit}
                        -> validación -> consulta a openFDA -> ranking

Necesita un archivo .env con la línea:  GEMINI_API_KEY=tu_clave
Ejecutar con:  python traductor.py
"""

import json
import os
import re
import time
from datetime import date

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

# Reutilizamos lo que ya escribimos en el paso 1
from explorar_api import consultar_api, mostrar_ranking

load_dotenv()  # lee el archivo .env y deja GEMINI_API_KEY disponible

MODELO = "gemini-3.5-flash"
MAX_INTENTOS = 2  # veces que dejamos a Gemini corregir una consulta inválida
MAX_REINTENTOS_SERVIDOR = 4  # veces que probamos si el servidor de Google está saturado

# --- Listas de campos válidos: nuestra "red de seguridad" contra campos inventados ---
CAMPOS_BUSQUEDA = {
    "device.generic_name",
    "device.brand_name",
    "device.manufacturer_d_name",
    "date_received",
    "event_type",
    "product_problems",
}
CAMPOS_COUNT = {
    "event_type.exact",
    "device.generic_name.exact",
    "device.brand_name.exact",
    "device.manufacturer_d_name.exact",
    "product_problems.exact",
}

# --- Instrucciones para el LLM: le explicamos los datos, porque él no los conoce ---
PROMPT_SISTEMA = f"""
Eres un traductor de preguntas en español a consultas de la API openFDA, endpoint device/event (base MAUDE).
Hoy es {date.today().isoformat()}.

Devuelves un JSON con tres campos:
- search: consulta en sintaxis openFDA.
- count: campo por el que agrupar y contar los informes.
- limit: cuántas filas del ranking devolver (entre 1 y 25; usa 10 si no se indica).

Campos que puedes usar en "search":
- device.generic_name: nombre genérico del dispositivo, en INGLÉS (p. ej. "clinical chemistry analyzer").
- device.brand_name: marca comercial.
- device.manufacturer_d_name: fabricante (p. ej. "ABBOTT LABORATORIES").
- date_received: fecha de recepción, formato [AAAAMMDD TO AAAAMMDD].
- event_type: Malfunction, Injury, Death u Other.
- product_problems: tipo de problema reportado, en inglés.

Valores válidos de "count" (elige uno según lo que pregunte el usuario):
{sorted(CAMPOS_COUNT)}

Reglas:
- Los valores de texto van en INGLÉS y entre comillas dobles cuando son una frase: device.generic_name:"chemistry analyzer".
- Une condiciones con AND. Para alternativas usa OR dentro de paréntesis.
- Para "analizadores de química clínica" usa:
  (device.generic_name:"chemistry analyzer" OR device.generic_name:"analyzer, chemistry")
- Si el usuario menciona un año, filtra con date_received:[AAAA0101 TO AAAA1231].
- Si pregunta por "fallos" o "problemas", cuenta por product_problems.exact.
- Si pregunta por "fabricantes", cuenta por device.manufacturer_d_name.exact.
- Si pregunta por "gravedad" o "tipos de evento", cuenta por event_type.exact.
- No uses ningún campo que no esté en las listas anteriores.
"""


class ConsultaFDA(BaseModel):
    """Forma exacta del JSON que debe devolver el LLM (las 'casillas fijas')."""

    search: str
    count: str
    limit: int


def validar_consulta(consulta: ConsultaFDA) -> str | None:
    """Devuelve un mensaje de error si la consulta no es válida, o None si está bien."""
    if consulta.count not in CAMPOS_COUNT:
        return f"count '{consulta.count}' no es válido. Opciones: {sorted(CAMPOS_COUNT)}"

    # Buscamos todos los "campo:" que aparecen en la búsqueda y comprobamos que existan
    campos_usados = re.findall(r"([A-Za-z_.]+):", consulta.search)
    for campo in campos_usados:
        if campo not in CAMPOS_BUSQUEDA:
            return f"el campo '{campo}' no existe. Campos válidos: {sorted(CAMPOS_BUSQUEDA)}"

    if not 1 <= consulta.limit <= 25:
        return "limit debe estar entre 1 y 25"

    return None


def llamar_a_gemini(cliente: genai.Client, mensaje: str) -> str:
    """Llama a Gemini. Si el servidor está saturado (error 5xx, p. ej. 503), espera y reintenta."""
    for intento in range(1, MAX_REINTENTOS_SERVIDOR + 1):
        try:
            respuesta = cliente.models.generate_content(
                model=MODELO,
                contents=mensaje,
                config=types.GenerateContentConfig(
                    system_instruction=PROMPT_SISTEMA,
                    response_mime_type="application/json",  # fuerza salida JSON
                    response_schema=ConsultaFDA,  # con esta forma exacta
                    temperature=0,  # sin creatividad: queremos respuestas estables
                ),
            )
            return respuesta.text
        except errors.ServerError as error:
            # Solo reintentamos errores del servidor. Otros (clave mala, modelo
            # inexistente...) no se arreglan esperando, así que se propagan.
            if intento == MAX_REINTENTOS_SERVIDOR:
                raise
            espera = 2**intento  # 2, 4, 8 segundos
            print(f"  (Gemini saturado [{error.code}]; reintento {intento} en {espera} s)")
            time.sleep(espera)


def traducir_pregunta(pregunta: str) -> ConsultaFDA:
    """Pide a Gemini la consulta; si no es válida, se lo decimos y reintenta."""
    cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    mensaje = pregunta

    for intento in range(1, MAX_INTENTOS + 1):
        texto = llamar_a_gemini(cliente, mensaje)
        consulta = ConsultaFDA(**json.loads(texto))

        error = validar_consulta(consulta)
        if error is None:
            return consulta

        print(f"  (intento {intento}: consulta no válida -> {error})")
        mensaje = (
            f"{pregunta}\n\nTu respuesta anterior fue rechazada: {error}\n"
            f"Anterior: {consulta.model_dump_json()}\nCorrígela."
        )

    raise ValueError("El LLM no produjo una consulta válida tras varios intentos.")


def main() -> None:
    pregunta = input("Pregunta: ").strip()

    print("\nTraduciendo con Gemini...")
    consulta = traducir_pregunta(pregunta)
    print(f"  search = {consulta.search}")
    print(f"  count  = {consulta.count}")
    print(f"  limit  = {consulta.limit}")

    print("\nConsultando openFDA...")
    resultados = consultar_api(
        {"search": consulta.search, "count": consulta.count, "limit": consulta.limit}
    )
    if resultados:
        mostrar_ranking(f"Resultados para: {pregunta}", resultados["results"])


if __name__ == "__main__":
    main()
