# Runbook del demo publico

Esta guia resume como operar FinSage LATAM una vez desplegado.

## URL publica

- [https://finsage-latam-production.up.railway.app/](https://finsage-latam-production.up.railway.app/)

## Estado esperado del sistema

Hay dos estados validos del demo:

1. `Online + con cuota`
   - la UI carga
   - `/recommend` responde
   - aparecen recomendaciones o una aclaracion util

2. `Online + sin cuota`
   - la UI carga
   - `/recommend` puede devolver `503`
   - el error debe ser explicito sobre cuota o proveedor

El segundo estado sigue siendo aceptable para portfolio si el objetivo es demostrar deploy, arquitectura y UX visible.

## Smoke test minimo

Haz estas pruebas despues de cada redeploy:

1. abrir la URL publica
2. confirmar que carga `FinSage LATAM`
3. revisar que el sidebar no muestre `API no alcanzable`
4. enviar una consulta corta
5. confirmar que la app devuelve una de estas dos cosas:
   - recomendacion real
   - aclaracion util con datos faltantes

## Consultas recomendadas

### Consulta ambigua

```text
Que es mejor una tarjeta o un credito personal?
```

Esperado:

- no debe romperse
- debe orientar
- debe pedir datos faltantes

### Consulta estructurada de tarjeta

```text
Gano 1.800.000 CLP, gasto 850.000 CLP y quiero una tarjeta con cashback para supermercado y compras del dia a dia. Me importa que la comision anual no sea tan alta.
```

Esperado:

- ranking de tarjetas
- reasoning trace
- caveats visibles

### Consulta estructurada de prestamo

```text
Gano 1.400.000 CLP, gasto 700.000 CLP y necesito un prestamo de 6 millones a 36 meses para consolidar deudas.
```

Esperado:

- orientacion util o ranking
- no debe repreguntar el objetivo si ya se entrego

## Si la cuota de Gemini se agota

Eso no requiere rollback del deploy.

Que hacer:

1. no tocar Dockerfile ni Railway
2. esperar a que vuelva la cuota
3. reintentar las consultas
4. cuando vuelva a responder, grabar el GIF y correr evals

Que no hacer:

- no desmontar el deploy
- no cambiar arquitectura por este motivo
- no ocultar el error del proveedor con mensajes vagos

## Antes de publicar en LinkedIn

Checklist:

1. la URL publica abre correctamente
2. al menos una consulta real responde con recomendaciones
3. el sidebar muestra metricas
4. el README ya contiene la URL publica
5. existe GIF demo o video corto
6. las metricas headline ya fueron actualizadas si corriste evals
