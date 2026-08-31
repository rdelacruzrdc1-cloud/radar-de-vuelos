# RADAR DE VUELOS

Agente que consulta precios de vuelos, guarda un histórico, lo muestra en un panel web y avisa por Telegram cuando detecta una oportunidad.

## Reglas del proyecto

- Todo va en la carpeta raíz. NO crear subcarpetas.
- NO usar frameworks: nada de React, Next, Flask o Django.
- Python: solo librería estándar + requests.
- HTML: un solo archivo, sin librerías externas.
- Las llaves SIEMPRE se leen del .env. NUNCA escribirlas en el código.
- Antes de crear un archivo nuevo, revisar si ya existe uno que sirva.

## Variables de entorno

FLIGHT_API_TOKEN    token de Travelpayouts
TELEGRAM_TOKEN      token entregado por BotFather
TELEGRAM_CHAT_ID    id del chat que recibirá los avisos

## Regla obligatoria antes de usar cualquier API

1. Haz UNA llamada de prueba y muéstrame la respuesta real.
2. Confirma el código HTTP y la estructura exacta del JSON.
3. Recién después escribe código usando los campos reales.
   Si la API no responde o devuelve algo diferente, PARA y avísame.

## Base de datos

SQLite, archivo radar.db.

- rutas: id, origen, destino, activa, precio_objetivo
- precios: id, ruta_id, precio, moneda, aerolinea, fecha_vuelo, fecha_consulta
  No insertar dos observaciones de la misma ruta en la misma fecha.

## Archivos futuros

- radar.py: consulta y guarda precios
- app.py: servidor y API interna
- index.html: panel web
- bot.py: Telegram y preguntas con Codex

## Cómo responder

- Cambios pequeños y explicados.
- No continuar si una prueba falla.
- Si algo no está claro, preguntar antes de asumir.
