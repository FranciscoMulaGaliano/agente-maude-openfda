"""
KPIs temporales a partir de la búsqueda que generó el agente. Solo usa la API de openFDA (sin Gemini).

Calcula tres cosas con SOLO 2 llamadas a openFDA:
    Llamada 1: informes por día, del periodo y del mismo periodo del año anterior.
               -> con ella sacamos la evolución mensual, el total y la variación interanual.
    Llamada 2: informes del periodo con lesión o muerte (solo pedimos el total).
               -> con él sacamos el % de informes graves.

Ejecutar con:  python kpis.py   (hace una prueba con una búsqueda de ejemplo)
"""

import re
from datetime import date, timedelta

from explorar_api import consultar_api

MESES_MAXIMOS = 12
FILAS_MAXIMAS = 999  # sin API key, limit=1000 da error 403

# Cláusula de fecha, con el AND que la une al resto
PATRON_FECHA = re.compile(r"date_received:\[(\d{8}) TO (\d{8})\]")
PATRON_FECHA_CON_AND = re.compile(
    r"\s+AND\s+date_received:\[[^\]]*\]|date_received:\[[^\]]*\]\s+AND\s+|date_received:\[[^\]]*\]"
)


def a_fecha(texto: str) -> date:
    """'20250131' -> date(2025, 1, 31)."""
    return date(int(texto[:4]), int(texto[4:6]), int(texto[6:8]))


def a_texto(fecha: date) -> str:
    """date(2025, 1, 31) -> '20250131' (formato de openFDA)."""
    return fecha.strftime("%Y%m%d")


def un_anio_antes(fecha: date) -> date:
    """Misma fecha un año antes. El 29 de febrero pasa al 28."""
    try:
        return fecha.replace(year=fecha.year - 1)
    except ValueError:
        return fecha.replace(year=fecha.year - 1, day=28)


def primer_dia_hace_meses(fecha: date, meses: int) -> date:
    """Día 1 del mes que está 'meses' meses antes de 'fecha'."""
    numero_mes = fecha.year * 12 + (fecha.month - 1) - meses
    return date(numero_mes // 12, numero_mes % 12 + 1, 1)


def separar_busqueda(search: str, hoy: date) -> tuple[str, date, date] | None:
    """Divide la búsqueda en (busqueda_sin_fecha, inicio, fin) del periodo a analizar.

    - Si la búsqueda ya trae fechas, las usamos (sin pasar de hoy).
    - Si no trae, usamos los últimos 12 meses.
    - Devuelve None si la fecha aparece de forma rara (no nos fiamos de cambiarla).
    """
    fechas = PATRON_FECHA.findall(search)
    if len(fechas) > 1:
        return None

    base = PATRON_FECHA_CON_AND.sub("", search).strip() if fechas else search.strip()
    if "date_received" in base:
        return None

    fin = hoy
    inicio = primer_dia_hace_meses(hoy, MESES_MAXIMOS - 1)
    if fechas:
        fin = min(a_fecha(fechas[0][1]), hoy)
        inicio = max(a_fecha(fechas[0][0]), primer_dia_hace_meses(fin, MESES_MAXIMOS - 1))
    if inicio > fin:
        return None
    return base, inicio, fin


def unir(base: str, *condiciones: str) -> str:
    """Une la búsqueda base con condiciones extra mediante AND."""
    partes = [f"({base})"] if base else []
    partes.extend(condiciones)
    return " AND ".join(partes)


def rango(inicio: date, fin: date) -> str:
    return f"date_received:[{a_texto(inicio)} TO {a_texto(fin)}]"


def lista_de_meses(inicio: date, fin: date) -> list[date]:
    """Primer día de cada mes entre inicio y fin, ambos incluidos."""
    meses = []
    mes = inicio.replace(day=1)
    while mes <= fin:
        meses.append(mes)
        mes = primer_dia_hace_meses(mes, -1)  # restar -1 meses = sumar uno
    return meses


def calcular_kpis(search: str, hoy: date | None = None) -> dict | None:
    """Devuelve los KPIs de la búsqueda, o None si no se pueden calcular (o no hay datos)."""
    hoy = hoy or date.today()
    separada = separar_busqueda(search, hoy)
    if separada is None:
        return None
    base, inicio, fin = separada
    inicio_anterior, fin_anterior = un_anio_antes(inicio), un_anio_antes(fin)

    # Llamada 1: informes por día, desde el año anterior hasta el final del periodo
    datos = consultar_api(
        {
            "search": unir(base, rango(inicio_anterior, fin)),
            "count": "date_received",
            "limit": FILAS_MAXIMAS,
        }
    )
    if not datos:
        return None

    por_mes = {mes: 0 for mes in lista_de_meses(inicio, fin)}
    total, anterior = 0, 0
    for fila in datos["results"]:  # en fechas la clave es 'time', no 'term'
        dia = a_fecha(fila["time"])
        if inicio <= dia <= fin:
            por_mes[dia.replace(day=1)] += fila["count"]
            total += fila["count"]
        elif inicio_anterior <= dia <= fin_anterior:
            anterior += fila["count"]  # los días intermedios no se cuentan

    if total == 0:
        return None

    # Llamada 2: informes con lesión o muerte (limit=1: solo nos interesa el total)
    datos_graves = consultar_api(
        {
            "search": unir(base, rango(inicio, fin), "(event_type:Injury OR event_type:Death)"),
            "limit": 1,
        }
    )
    graves = datos_graves["meta"]["results"]["total"] if datos_graves else 0

    return {
        "mensual": [{"mes": mes, "informes": cifra} for mes, cifra in por_mes.items()],
        "inicio": inicio,
        "fin": fin,
        "total": total,
        "anterior": anterior,
        "variacion": (total - anterior) / anterior * 100 if anterior else None,
        "graves": graves,
        "porcentaje_graves": graves / total * 100,
        # Si el periodo acaba a mitad de mes, el último punto está incompleto
        "ultimo_mes_incompleto": (fin + timedelta(days=1)).month == fin.month,
    }


def main() -> None:
    busqueda = 'device.generic_name:"chemistry analyzer" AND date_received:[20250101 TO 20251231]'
    kpis = calcular_kpis(busqueda)
    if kpis is None:
        print("No se pudieron calcular los KPIs.")
        return
    for mes in kpis["mensual"]:
        print(f"{mes['mes']:%Y-%m}  {mes['informes']:>6}")
    print(f"Total {kpis['total']} | año anterior {kpis['anterior']} | variación {kpis['variacion']}")
    print(f"Lesión o muerte: {kpis['graves']} ({kpis['porcentaje_graves']:.1f} %)")


if __name__ == "__main__":
    main()
