"""
PASO 4 - Interfaz web con Streamlit.

Una sola pantalla: la pregunta, 3 tarjetas con cifras clave, la búsqueda que generó Gemini,
un gráfico de barras, el resumen y, abajo, los KPIs temporales (evolución mensual,
variación interanual y % de informes graves), que salen solo de openFDA.

Ejecutar con:  streamlit run app.py
"""

import altair as alt
import pandas as pd
import streamlit as st
from google.genai import errors

from kpis import calcular_kpis
from resumidor import (
    consultar_y_limpiar,
    contar_total,
    formatear_miles,
    resumir,
    traducir_categorias,
)
from traductor import traducir_pregunta

PREGUNTA_EJEMPLO = "¿qué fallos se reportan más en analizadores de química clínica en 2025?"
COLOR_DESTACADO = "#2a78d6"  # azul: barras destacadas y línea mensual
COLOR_RESTO = "#97968f"
COLOR_TEXTO = "#52514e"
BARRAS_DESTACADAS = 3

# Título de la segunda tarjeta según el campo que se cuenta
TITULO_MAS_FRECUENTE = {
    "product_problems.exact": "Problema más reportado",
    "device.manufacturer_d_name.exact": "Fabricante con más informes",
    "event_type.exact": "Tipo de evento más frecuente",
    "device.generic_name.exact": "Dispositivo más reportado",
    "device.brand_name.exact": "Marca más reportada",
}

st.set_page_config(page_title="Agente MAUDE", layout="wide")


# Caché de 1 hora: repetir la pregunta no gasta llamadas de Gemini.
@st.cache_data(ttl=3600, show_spinner=False)
def ejecutar_agente(pregunta: str):
    """Ejecuta todo el proceso. Devuelve (consulta, filas, total, resumen, cifras_dudosas)."""
    consulta = traducir_pregunta(pregunta)
    filas = consultar_y_limpiar(consulta)
    if not filas:
        return consulta.model_dump(), [], None, "", []

    filas = traducir_categorias(filas, consulta.count)  # nombres en español
    total = contar_total(consulta)  # total real, consultando sin 'count'
    resumen, dudosas = resumir(pregunta, consulta, filas, total)
    return consulta.model_dump(), filas, total, resumen, dudosas


# Caché por búsqueda. Cuesta 2 llamadas a openFDA por pregunta nueva (límite sin key: 1.000/día).
@st.cache_data(ttl=3600, show_spinner=False)
def obtener_kpis(search: str):
    return calcular_kpis(search)


def formatear_porcentaje(valor: float, con_signo: bool = False) -> str:
    """12.34 -> '12,3 %'  (con_signo=True: '+12,3 %')."""
    signo = "+" if con_signo else ""
    return f"{valor:{signo}.1f}".replace(".", ",") + " %"


def dibujar_tarjetas(consulta: dict, filas: list[dict], total: int | None) -> None:
    """Tres tarjetas: total de informes, categoría más frecuente y peso de las 3 primeras."""
    primera = filas[0]
    suma_primeras = sum(fila["count"] for fila in filas[:BARRAS_DESTACADAS])

    if total:
        porcentaje = formatear_porcentaje(suma_primeras / total * 100)
    else:
        porcentaje = "—"

    tarjeta1, tarjeta2, tarjeta3 = st.columns(3)
    tarjeta1.metric(
        "Total de informes",
        formatear_miles(total) if total else "—",
        help="Número real de informes que cumplen la búsqueda, consultado a la API sin agrupar.",
    )
    tarjeta2.metric(
        TITULO_MAS_FRECUENTE.get(consulta["count"], "Categoría más frecuente"),
        primera["term"],
        delta=f"{formatear_miles(primera['count'])} informes",
        delta_color="off",
        delta_arrow="off",
    )
    tarjeta3.metric(
        f"Peso de las {BARRAS_DESTACADAS} primeras",
        porcentaje,
        help=(
            "Suma de informes de las 3 primeras categorías dividida entre el total de informes. "
            "Un mismo informe puede tener varios problemas, así que en algunos rankings "
            "las categorías se solapan y este porcentaje es aproximado."
        ),
    )


def dibujar_grafico(filas: list[dict]) -> None:
    """Barras horizontales: las 3 primeras en azul, el resto en gris, con la cifra al final."""
    datos = pd.DataFrame(filas)
    datos["etiqueta"] = datos["count"].apply(formatear_miles)
    datos["grupo"] = ["destacada" if i < BARRAS_DESTACADAS else "resto" for i in range(len(datos))]

    base = alt.Chart(datos).encode(
        # sort=lista respeta el orden de mayor a menor que ya trae el ranking
        y=alt.Y(
            "term:N",
            sort=list(datos["term"]),
            title=None,
            axis=alt.Axis(labelLimit=400, labelFontSize=13, labelColor=COLOR_TEXTO, ticks=False, domain=False),
        ),
        x=alt.X("count:Q", title=None, axis=None),
    )
    barras = base.mark_bar(size=18, cornerRadiusEnd=4).encode(
        color=alt.Color(
            "grupo:N",
            scale=alt.Scale(domain=["destacada", "resto"], range=[COLOR_DESTACADO, COLOR_RESTO]),
            legend=None,  # sin leyenda: el subtítulo del gráfico explica el azul
        )
    )
    cifras = base.mark_text(align="left", dx=6, fontSize=13, color=COLOR_TEXTO).encode(text="etiqueta:N")

    grafico = (barras + cifras).properties(height=36 * len(datos)).configure_view(stroke=None)
    st.altair_chart(grafico, width="stretch")


def dibujar_linea_mensual(kpis: dict) -> None:
    """Línea con los informes de cada mes del periodo."""
    datos = pd.DataFrame(kpis["mensual"])
    datos["etiqueta"] = datos["informes"].apply(formatear_miles)

    base = alt.Chart(datos).encode(
        # %m/%y y no nombres de mes: Altair los pondría en inglés
        x=alt.X("mes:T", title=None, axis=alt.Axis(format="%m/%y", labelColor=COLOR_TEXTO, grid=False)),
        y=alt.Y("informes:Q", title=None, axis=alt.Axis(labelColor=COLOR_TEXTO, tickCount=4)),
        tooltip=[alt.Tooltip("mes:T", title="Mes", format="%m/%Y"), alt.Tooltip("etiqueta:N", title="Informes")],
    )
    linea = base.mark_line(color=COLOR_DESTACADO, strokeWidth=2.5, point=alt.OverlayMarkDef(color=COLOR_DESTACADO))

    grafico = linea.properties(height=260).configure_view(stroke=None)
    st.altair_chart(grafico, width="stretch")


def dibujar_kpis(kpis: dict) -> None:
    """Fila inferior: línea mensual (izquierda) y dos tarjetas (derecha)."""
    anio_actual = kpis["fin"].year
    columna_linea, columna_tarjetas = st.columns([3, 2], gap="large")

    with columna_linea:
        st.markdown("**Evolución mensual de informes**")
        periodo = f"{kpis['inicio']:%d/%m/%Y} – {kpis['fin']:%d/%m/%Y}"
        aviso = " El último mes está incompleto." if kpis["ultimo_mes_incompleto"] else ""
        st.caption(f"Periodo: {periodo}.{aviso}")
        dibujar_linea_mensual(kpis)

    with columna_tarjetas:
        variacion = kpis["variacion"]
        st.metric(
            "Variación respecto al mismo periodo del año anterior",
            formatear_porcentaje(variacion, con_signo=True) if variacion is not None else "—",
            delta=f"{formatear_miles(kpis['total'])} informes frente a {formatear_miles(kpis['anterior'])}",
            delta_color="off",  # más informes no es bueno ni malo: sin verde/rojo
            delta_arrow="off",
            help=(
                "Compara el periodo de la gráfica con las mismas fechas del año anterior. "
                "Más informes no significa más fallos: también influye cuántos equipos hay "
                "instalados y cuánto se notifica."
            ),
        )
        st.metric(
            "Informes con lesión o muerte",
            formatear_porcentaje(kpis["porcentaje_graves"]),
            delta=f"{formatear_miles(kpis['graves'])} de {formatear_miles(kpis['total'])} informes",
            delta_color="off",
            delta_arrow="off",
            help=(
                f"Informes del periodo cuyo tipo de evento es Injury o Death, "
                f"entre todos los del periodo ({anio_actual})."
            ),
        )


st.title("Agente de incidencias de dispositivos médicos")
st.caption("Pregunta en español → Gemini la traduce a una consulta de openFDA (base MAUDE) → gráfico y resumen.")

# st.form hace que Enter envíe la pregunta, igual que el botón
with st.form("formulario"):
    pregunta = st.text_input("Pregunta", value=PREGUNTA_EJEMPLO)
    enviado = st.form_submit_button("Consultar", type="primary")

if enviado and pregunta.strip():
    try:
        with st.spinner("Traduciendo, consultando y resumiendo..."):
            consulta, filas, total, resumen, dudosas = ejecutar_agente(pregunta.strip())
    except errors.ClientError as error:
        if error.code == 429:  # cuota agotada
            st.error("Se ha agotado la cuota gratuita de Gemini por hoy. Inténtalo más tarde o cambia de modelo.")
        else:
            st.error(f"Gemini rechazó la petición: {error}")
        st.stop()
    except Exception as error:
        st.error(f"No se pudo completar la consulta: {error}")
        st.stop()

    if not filas:
        st.warning("openFDA no devolvió resultados para esta pregunta. Prueba a reformularla.")
        st.stop()

    dibujar_tarjetas(consulta, filas, total)

    st.markdown("**Búsqueda generada por el agente**")
    st.code(f"search = {consulta['search']}\ncount  = {consulta['count']}", language=None, wrap_lines=True)

    columna_grafico, columna_resumen = st.columns([3, 2], gap="large")
    with columna_grafico:
        st.markdown("**Informes por categoría**")
        st.caption(f"En azul, las {BARRAS_DESTACADAS} primeras.")
        dibujar_grafico(filas)
    with columna_resumen:
        st.markdown("**Resumen**")
        st.write(resumen)
        if dudosas:
            st.warning(f"Estas cifras del resumen no aparecen en los datos: {dudosas}")

    # Si los KPIs fallan, el resto de la pantalla sigue funcionando
    try:
        kpis = obtener_kpis(consulta["search"])
    except Exception as error:
        kpis = None
        st.caption(f"No se pudieron calcular los KPIs temporales: {error}")
    if kpis:
        dibujar_kpis(kpis)

    st.caption("Fuente: openFDA, base MAUDE. Son informes recibidos por la FDA, no tasas de fallos.")
