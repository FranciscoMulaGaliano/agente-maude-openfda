"""
PASO 1 - Exploración de la API de openFDA (incidentes con dispositivos médicos / base MAUDE)

Objetivo: entender qué datos devuelve la API ANTES de meter ninguna IA.
No necesita API key (sin key se permiten ~1000 peticiones al día).

Ejecutar con:  python explorar_api.py
"""

import requests

BASE_URL = "https://api.fda.gov/device/event.json"

# Búsqueda: dispositivos cuyo nombre genérico contiene "analyzer",
# recibidos por la FDA durante 2025.
BUSQUEDA = 'device.generic_name:analyzer AND date_received:[20250101 TO 20251231]'


def consultar_api(params: dict) -> dict | None:
    """Hace la petición a openFDA y devuelve el JSON, o None si no hay resultados."""
    respuesta = requests.get(BASE_URL, params=params, timeout=30)

    # openFDA devuelve 404 cuando la búsqueda no encuentra nada
    if respuesta.status_code == 404:
        print("  (sin resultados para esta búsqueda)")
        return None

    respuesta.raise_for_status()  # lanza error si hay otro problema (400, 500...)
    return respuesta.json()


def obtener_eventos(busqueda: str, limite: int = 3) -> list[dict]:
    """Descarga algunos informes de incidentes completos."""
    datos = consultar_api({"search": busqueda, "limit": limite})
    return datos["results"] if datos else []


def contar_por_campo(busqueda: str, campo: str, top: int = 10) -> list[dict]:
    """Pide a la API que agrupe los informes por un campo y los cuente."""
    datos = consultar_api({"search": busqueda, "count": campo, "limit": top})
    return datos["results"] if datos else []


def mostrar_evento(evento: dict, numero: int) -> None:
    """Imprime un resumen legible de un informe."""
    dispositivo = evento.get("device", [{}])[0]
    textos = evento.get("mdr_text", [])
    descripcion = textos[0].get("text", "") if textos else "(sin descripción)"

    print(f"\n--- Evento {numero} ---")
    print(f"Fecha recibido : {evento.get('date_received', '?')}")
    print(f"Tipo de evento : {evento.get('event_type', '?')}")
    print(f"Marca          : {dispositivo.get('brand_name', '?')}")
    print(f"Fabricante     : {dispositivo.get('manufacturer_d_name', '?')}")
    print(f"Problemas      : {', '.join(evento.get('product_problems', [])) or '?'}")
    print(f"Descripción    : {descripcion[:300]}...")


def mostrar_ranking(titulo: str, resultados: list[dict]) -> None:
    """Imprime una tabla sencilla término -> número de informes."""
    print(f"\n=== {titulo} ===")
    for fila in resultados:
        print(f"{fila['count']:>7}  {fila['term']}")


def main() -> None:
    print("Consultando openFDA...\n")

    # 1) Unos cuantos informes completos, para ver la estructura
    eventos = obtener_eventos(BUSQUEDA, limite=3)
    for i, evento in enumerate(eventos, start=1):
        mostrar_evento(evento, i)

    # 2) Rankings: la API cuenta por nosotros
    mostrar_ranking(
        "Tipos de evento",
        contar_por_campo(BUSQUEDA, "event_type.exact"),
    )
    mostrar_ranking(
        "Top 10 tipos de dispositivo",
        contar_por_campo(BUSQUEDA, "device.generic_name.exact"),
    )
    mostrar_ranking(
        "Top 10 problemas reportados",
        contar_por_campo(BUSQUEDA, "product_problems.exact"),
    )


if __name__ == "__main__":
    main()
