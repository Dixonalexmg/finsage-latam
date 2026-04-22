# Guia de deploy en Railway, paso a paso

Esta guia esta escrita para una primera vez. La idea es que puedas levantar
FinSage LATAM sin asumir experiencia previa con Railway.

## Que vas a desplegar

La topologia recomendada para portfolio es esta:

- `1 servicio publico`
- `1 contenedor`
- `1 URL publica`
- `Streamlit` expuesto en `/`
- `FastAPI` corriendo dentro del mismo contenedor en `127.0.0.1:8000`

Esto evita complejidad innecesaria de CORS y hace mas facil mostrar la demo.

## Antes de empezar

Debes tener listo:

1. Una cuenta en [GitHub](https://github.com/).
2. Una cuenta en [Railway](https://railway.app/).
3. Este proyecto subido a un repositorio de GitHub.
4. Una `GEMINI_API_KEY` valida.

Archivos de deploy ya incluidos en el repo:

- [railway.toml](/C:/Users/Usuario/Documents/finsage-latam/railway.toml)
- [Dockerfile](/C:/Users/Usuario/Documents/finsage-latam/Dockerfile)
- [docker-compose.yml](/C:/Users/Usuario/Documents/finsage-latam/docker-compose.yml)
- [src/deploy.py](/C:/Users/Usuario/Documents/finsage-latam/src/deploy.py)

## Paso 0: validar localmente

Haz esto antes de subir a Railway. Si falla local, va a fallar arriba tambien.

### 0.1 Instala dependencias

```powershell
uv sync
```

### 0.2 Carga tu API key en la terminal actual

```powershell
$env:GEMINI_API_KEY="TU_CLAVE_REAL"
```

### 0.3 Corre checks de calidad

```powershell
uv run ruff check .
uv run mypy
uv run pytest
```

### 0.4 Levanta la API

```powershell
uv run python -m src.api.main
```

Abre [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) y confirma:

- `status: "ok"`
- `recommendations_ready: true`
- `missing_env: []`

### 0.5 Levanta la UI en otra terminal

```powershell
uv run streamlit run src/ui/app.py
```

Abre [http://127.0.0.1:8501](http://127.0.0.1:8501) y prueba una consulta real.

Si esto no funciona localmente, no sigas al deploy todavia.

## Paso 1: subir el repo a GitHub

Si todavia no lo hiciste:

1. Crea un repositorio nuevo en GitHub.
2. Sube este proyecto.
3. Confirma que en GitHub aparezcan estos archivos en la raiz:
   - `Dockerfile`
   - `railway.toml`
   - `README.md`
   - `src/`

## Paso 2: crear el proyecto en Railway

1. Entra a [Railway](https://railway.app/).
2. Haz click en `New Project`.
3. Elige `Deploy from GitHub repo`.
4. Autoriza a Railway a ver tus repositorios si te lo pide.
5. Selecciona el repo `finsage-latam`.
6. Espera a que Railway cree el proyecto.

No cambies el root directory si el repo ya esta en la raiz.

## Paso 3: confirmar que use Docker

Dentro del proyecto:

1. Entra al servicio creado.
2. Ve a la pestaña de `Settings`.
3. Revisa que Railway detecte el `Dockerfile`.

Este repo ya trae:

- builder por `Dockerfile`
- healthcheck en `/`
- restart policy configurada

No necesitas escribir un start command manual si usas la configuracion recomendada.

## Paso 4: cargar variables de entorno

En Railway:

1. Abre el servicio.
2. Ve a `Variables`.
3. Agrega cada variable con `New Variable`.

### Variables obligatorias

- `GEMINI_API_KEY`

### Variables opcionales recomendadas

- `LOGFIRE_TOKEN`
- `FINSAGE_MAX_REQUEST_SIZE_BYTES`

### Variables que normalmente NO necesitas tocar

- `PORT`
- `FINSAGE_API_HOST`
- `FINSAGE_API_PORT`
- `FINSAGE_API_URL`

Por que:

- Railway inyecta `PORT` automaticamente.
- El launcher del proyecto hace que Streamlit se publique en ese `PORT`.
- La API queda interna en `127.0.0.1:8000`.
- La UI ya sabe hablar con la API interna desde el mismo contenedor.

## Paso 5: lanzar el primer deploy

Al guardar variables, Railway normalmente dispara un redeploy automatico.

Si no ocurre:

1. Ve a la pestaña `Deployments`.
2. Haz click en `Redeploy`.

Que deberias ver:

1. Fase de build.
2. Fase de start.
3. Estado `Success`.

Si falla el build, abre el log del deploy y revisa la primera linea de error real.

## Paso 6: abrir la URL publica

Cuando el deploy termine:

1. Railway mostrara una URL publica.
2. Abrela en el navegador.
3. Deberias ver la UI de Streamlit.

La pagina principal que debe responder es `/`, no `/health`.

URL real del deploy actual:

- [https://finsage-latam-production.up.railway.app/](https://finsage-latam-production.up.railway.app/)

## Paso 7: smoke test post deploy

Haz estas verificaciones en este orden:

### 7.1 Confirmar que la UI carga

Debes ver:

- titulo `FinSage LATAM`
- chat
- sidebar de telemetria

### 7.2 Confirmar que la API interna responde

En el sidebar no debe aparecer:

- `API no alcanzable`

### 7.3 Confirmar el estado de health

Como esta topologia publica Streamlit en `/`, el health tecnico de FastAPI no queda expuesto por defecto en la URL principal.

Para portfolio esto es suficiente:

- la UI debe cargar
- la recomendacion debe funcionar
- el sidebar debe reflejar metricas

Si luego separas UI y API en dos servicios, ahi si conviene exponer `/health` publicamente en la API.

### 7.4 Hacer una consulta real

Prueba con algo como:

```text
Gano 1.500.000 CLP y quiero una tarjeta con cashback para compras del dia a dia
```

Confirma:

- se renderiza una respuesta
- aparecen recomendaciones
- se muestra el JSON estructurado
- el sidebar sube `Queries`, `Recomendaciones` y `Exito`

Nota realista para free tier:

- si Gemini no tiene cuota disponible en ese momento, la UI igual debe cargar
- el deploy debe seguir `Online`
- `/recommend` puede responder `503` con detalle del proveedor

## Paso 8: dejarlo listo para README y LinkedIn

Cuando ya funcione la URL publica:

1. Copia la URL publica de Railway.
2. Reemplaza el placeholder del README en la seccion `Demo desplegado`.
3. Graba un video corto.
4. Genera `assets/demo.gif`.
5. Corre evals con clave real.
6. Pega las 3 metricas headline reales en el README.

## Paso 9: checklist final de publicacion

Usa este checklist antes de compartirlo:

1. `uv run ruff check .`
2. `uv run mypy`
3. `uv run pytest`
4. Validar que la demo local funcione.
5. Confirmar `GEMINI_API_KEY` en Railway.
6. Esperar deploy exitoso.
7. Abrir la URL publica.
8. Hacer una consulta real.
9. Confirmar que el sidebar refleje metricas.
10. Actualizar `README.md` con la URL publica.
11. Grabar GIF.
12. Preparar publicacion de LinkedIn con:
   - problema
   - stack
   - demo
   - metricas
   - link en vivo

## Troubleshooting rapido

### El deploy queda online pero Railway no muestra dominio al principio

Puede pasar que el contenedor ya este sirviendo trafico, pero el dominio bonito de Railway no aparezca inmediatamente.

Que revisar:

1. entra al servicio
2. abre `Settings`
3. busca `Networking` o `Domains`
4. si hace falta, genera el dominio publico desde ahi

Senal de que el deploy si esta vivo:

- en logs veras algo como `Local URL: http://localhost:8080`
- y una `External URL` temporal servida por el contenedor

En el deploy actual, el dominio publico final quedo en:

- [https://finsage-latam-production.up.railway.app/](https://finsage-latam-production.up.railway.app/)

### La UI abre pero recomendar falla

Revisa:

- que `GEMINI_API_KEY` este cargada en Railway
- que el deploy mas reciente haya quedado `Success`
- que no hayas agotado cuota del free tier

### El sidebar muestra `API no alcanzable`

Revisa:

- que el contenedor haya arrancado completo
- que no haya crash loops en `Deployments`
- que el launcher por defecto no haya sido reemplazado por un comando incorrecto

### La recomendacion responde pero las metricas no cambian

Eso ya no deberia pasar con la version actual. Si vuelve a ocurrir:

1. refresca la pagina
2. mira logs del servicio
3. verifica que la recomendacion haya terminado con exito

### Railway hace redeploy pero sigue usando una version vieja

Haz esto:

1. confirma que hiciste push a GitHub
2. entra a `Deployments`
3. fuerza `Redeploy`

## Configuracion alternativa: UI y API separadas

No es la recomendada para primera vez, pero si luego quieres separar servicios:

### Servicio API

Start command:

```bash
python -m src.deploy --service api
```

Variables:

- `PORT=8000`
- `FINSAGE_API_HOST=0.0.0.0`
- `FINSAGE_API_PORT=8000`
- `GEMINI_API_KEY`
- `LOGFIRE_TOKEN` opcional
- `FINSAGE_CORS_ALLOW_ORIGINS=https://TU-UI.up.railway.app`

### Servicio UI

Start command:

```bash
python -m src.deploy --service ui
```

Variables:

- `FINSAGE_API_URL=https://TU-API.up.railway.app`

Esta topologia agrega complejidad de CORS, asi que para portfolio conviene
quedarse con la opcion de un solo servicio.
