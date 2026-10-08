"""
PASO 4 - Interfaz web con Streamlit.

Una sola pantalla: la pregunta, 3 tarjetas con cifras clave, la búsqueda que generó Gemini,
un gráfico de barras y el resumen.

Ejecutar con:  streamlit run app.py
"""

import altair as alt
import pandas as pd
import streamlit as st
from google.genai import errors

# Reutilizamos todo lo de los pasos anteriores
from resumidor import (
    consultar_y_limpiar,
    contar_total,
    formatear_miles,
    resumir,
    traducir_categorias,
)
from traductor import traducir_pregunta

PREGUNTA_EJEMPLO = "¿qué fallos se reportan más en analizadores de química clínica en 2025?"
COLOR_DESTACADO = "#2a78d6"  # azul: las 3 primeras barras
COLOR_RESTO = "#97968f"  # gris: el resto
COLOR_TEXTO = "#52514e"
BARRAS_DESTACADAS = 3

# Título de la segunda tarjeta según lo que se esté contando
TITULO_MAS_FRECUENTE = {
    "product_problems.exact": "Problema más reportado",
    "device.manufacturer_d_name.exact": "Fabricante con más informes",
    "event_type.exact": "Tipo de evento más frecuente",
    "device.generic_name.exact": "Dispositivo más reportado",
    "device.brand_name.exact": "Marca más reportada",
}

st.set_page_config(page_title="Agente MAUDE", layout="wide")


# st.cache_data guarda el resultado de cada pregunta durante 1 hora:
# repetir la misma pregunta no vuelve a gastar llamadas de Gemini.
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


def dibujar_tarjetas(consulta: dict, filas: list[dict], total: int | None) -> None:
    """Tres tarjetas: total de informes, categoría más frecuente y peso de las 3 primeras."""
    primera = filas[0]
    suma_primeras = sum(fila["count"] for fila in filas[:BARRAS_DESTACADAS])

    if total:
        porcentaje = f"{suma_primeras / total * 100:.1f}".replace(".", ",") + " %"
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


# --------------------------------------------------------------------------
# Pantalla
# --------------------------------------------------------------------------
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
        if error.code == 429:  # 429 = demasiadas peticiones / cuota agotada
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

    st.caption("Fuente: openFDA, base MAUDE. Son informes recibidos por la FDA, no tasas de fallos.")
