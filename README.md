# Agente de incidencias de dispositivos médicos (openFDA / MAUDE)

Escribes una pregunta en español y un agente la traduce a una consulta de la API pública de la FDA, ejecuta la consulta y te devuelve un gráfico y un resumen en español.

> *¿Qué fallos se reportan más en analizadores de química clínica en 2025?*

<!-- Cuando tengas las capturas, súbelas a una carpeta "docs/" y descomenta esta línea:
![Captura de la aplicación](docs/captura.png)
-->

## Qué hace

1. **Traduce** la pregunta en español a una consulta de la API openFDA (endpoint `device/event`, base de datos MAUDE) usando un LLM.
2. **Consulta** la API de la FDA con esa búsqueda.
3. **Muestra** una pantalla con:
   - 3 tarjetas: total de informes, categoría más frecuente y peso de las 3 primeras.
   - La búsqueda exacta que generó el agente (para poder revisarla).
   - Un gráfico de barras, con las 3 primeras categorías destacadas.
   - Un resumen de 3-4 frases en español.

## Cómo funciona

```
Pregunta en español
        │
        ▼
 traductor.py ──► Gemini devuelve JSON {search, count, limit}
        │          └─ el código valida los campos antes de usarlos
        ▼
 openFDA (device/event) ──► ranking de categorías + total real de informes
        │
        ▼
 resumidor.py ──► limpia nombres de fabricantes, traduce categorías (diccionario)
        │          y Gemini redacta el resumen
        │          └─ el código comprueba que las cifras del resumen existen en los datos
        ▼
 app.py (Streamlit) ──► tarjetas + gráfico + resumen
```

| Archivo | Para qué sirve |
|---|---|
| `explorar_api.py` | Exploración de la API sin IA: ver qué datos devuelve openFDA. |
| `traductor.py` | Pregunta en español → parámetros de la API (con validación). |
| `resumidor.py` | Limpieza de datos, total real, traducción de categorías y resumen con comprobación de cifras. |
| `app.py` | Interfaz web con Streamlit. |

### Decisiones de diseño

- **El LLM no toca los datos.** Solo traduce la pregunta y redacta el resumen. Las consultas, los totales y las cifras salen de la API y del código.
- **Se valida lo que devuelve el LLM.** Si Gemini inventa un campo que no existe en openFDA, el código lo detecta, se lo indica y le deja corregirlo antes de llamar a la API.
- **Se comprueban las cifras del resumen.** Cada número que aparece en el texto debe estar en los datos; si no, se pide una corrección o se muestra un aviso.
- **Total real, no suma de filas.** El total de informes se consulta a la API sin agrupar, porque un mismo informe puede aparecer en varias categorías.
- **Limpieza de datos en código, no en el LLM.** Las variantes de un fabricante (`BECKMAN COULTER, INC.`, `INC`, `INC.`) se unen con reglas fijas.
- **Traducción de categorías con un diccionario fijo** (43 términos habituales). Lo que no esté en el diccionario se muestra en inglés.

## Instalación

Necesitas Python 3.10 o superior (desarrollado y probado con Python 3.14) y una clave gratuita de Gemini.

1. Clona el repositorio y entra en la carpeta:
   ```
   git clone https://github.com/FranciscoMulaGaliano/agente-maude-openfda.git
   cd agente-maude-openfda
   ```
2. Crea y activa un entorno virtual (en Windows con PowerShell):
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
   En macOS/Linux: `source .venv/bin/activate`
3. Instala las librerías:
   ```
   pip install -r requirements.txt
   ```
4. Consigue una clave gratuita en [Google AI Studio](https://aistudio.google.com/apikey).
5. Copia `.env.example` como `.env` y pega tu clave:
   ```
   GEMINI_API_KEY=tu_clave
   ```

## Uso

```
streamlit run app.py
```

Se abre en el navegador (`http://localhost:8501`). Escribe tu pregunta y pulsa **Consultar**.

También se puede probar sin interfaz:

```
python explorar_api.py    # explora la API de openFDA, sin IA
python resumidor.py       # flujo completo en la terminal
```

El modelo de Gemini se cambia en una sola línea: la constante `MODELO` de `traductor.py`. Los nombres y la disponibilidad de los modelos cambian con frecuencia.

## Limitaciones

- **MAUDE son informes, no tasas de fallos.** Un número alto de informes no significa que un equipo o fabricante sea peor: depende de cuántos equipos haya instalados, y MAUDE no tiene esa cifra. Los informes pueden estar duplicados o incompletos y no prueban que el dispositivo causara el problema.
- **Las categorías se solapan.** Un informe puede tener varios problemas, así que sus cifras no se pueden sumar y el porcentaje de las 3 primeras es aproximado en los rankings de problemas.
- **Datos sucios.** Los datos son texto libre con erratas y variantes de nombre. La limpieza es simple (mayúsculas, puntuación, sufijos como `INC`) y no une casos como `ABBOTT` con `ABBOTT LABORATORIES`.
- **Un filtro de dispositivo muy sencillo.** El agente busca por nombre genérico (por ejemplo "chemistry analyzer"). Otras formas de nombrar el mismo tipo de equipo pueden quedar fuera. Usar el código de producto de la FDA sería más fiable.
- **La comprobación de cifras no es completa.** Detecta números que no están en los datos, pero no detecta una cifra real atribuida a la categoría equivocada.
- **Cuota gratuita de Gemini limitada.** Cada pregunta usa 2 llamadas, y el nivel gratuito tiene un límite diario por modelo que puede agotarse. Las preguntas repetidas se guardan en caché durante 1 hora.
- **Solo consulta el endpoint `device/event`** y solo los campos permitidos en `traductor.py`.
- **Proyecto educativo.** No debe usarse para tomar decisiones médicas ni regulatorias. openFDA avisa de que sus datos no están validados.

## Datos

Datos públicos de la [API openFDA](https://open.fda.gov/apis/device/event/) (base MAUDE, Manufacturer and User Facility Device Experience). No se usan datos de clientes ni documentación interna de ninguna empresa.

## Cómo se hizo

Este proyecto lo construí paso a paso con la ayuda de **Claude Code** (Anthropic) como asistente de programación: desde explorar la API hasta la interfaz. En ejecución, el LLM que usa la aplicación es Gemini.

## Autor

**Francisco Mula Galiano** — ingeniero biomédico (Universidad de Málaga). [LinkedIn](https://www.linkedin.com/in/francisco-mula-galiano-/). Field Service Engineer especializado en equipos de diagnóstico clínico de Roche Diagnostics y Johnson & Johnson (mantenimiento preventivo y correctivo). Proyecto de portfolio para aprender a combinar APIs públicas, LLMs y visualización de datos.

Este proyecto es personal y no tiene relación con ninguna de esas empresas. Solo usa datos públicos de openFDA.

## Licencia

[MIT](LICENSE).
