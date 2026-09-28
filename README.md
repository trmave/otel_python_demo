# otel_python_demo

Demo de **observabilidad con OpenTelemetry** usando Python + Flask: tres
microservicios que simulan una mini tienda (`load-generator` → `payments` →
`inventory`) y envian **trazas y logs** por OTLP a un backend **Grafana LGTM**
(Grafana + Tempo + Loki + Prometheus) que corre en el mismo servidor.

Este README está escrito para leerse de arriba a abajo: primero qué es el
proyecto, y luego **línea por línea cómo funciona OpenTelemetry dentro del
código**, que es la parte que te interesa si estás aprendiendo.

---

## 1. La historia que este sistema cuenta

Un generador de carga "compra" un producto cada segundo. El servicio de
pagos **no cobra a ciegas**: primero le pregunta al inventario si el
producto existe y tiene stock, cobra (con una latencia simulada de pasarela)
y guarda el pago. A veces pide productos inexistentes o sin stock a
proposito, para generar errores realistas.

Todo ese recorrido deja **huellas** (trazas y logs) que puedes ver unidas en
Grafana, como si fuera una grabación de lo que pasó por dentro.

## 2. Arquitectura

```
load-generator  --HTTP-->  payments  --HTTP-->  inventory
     |                        |                    |
     +------ OTLP gRPC (4317): trazas + logs -----+
                              |
                    Grafana LGTM (contenedor big-bear-otel-lgtm)
                    ├─ Tempo     <- guarda trazas
                    ├─ Loki      <- guarda logs
                    ├─ Prometheus<- métricas derivadas de las trazas
                    └─ Grafana   <- UI en http://192.168.3.60:3029
```

![Diagrama de arquitectura](docs/images/architecture.png)

Detalle en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
diagrama editable en [docs/diagrama-arquitectura.drawio](docs/diagrama-arquitectura.drawio).

## 3. Servicios

| Servicio         | Tecnología | Puerto | Rol                                                     |
| ---------------- | ---------- | ------ | ------------------------------------------------------- |
| `inventory`      | Flask      | 5001   | Catálogo de productos y verificación de stock           |
| `payments`       | Flask      | 5000   | Recibe pagos; **llama a inventory** antes de cobrar     |
| `load-generator` | Python     | -      | Dispara pagos aleatorios cada ~1 s contra `payments`    |

---

## 4. Cómo funciona OpenTelemetry en este proyecto (sección principal)

### 4.1 Los cuatro conceptos que lo gobiernan todo

| Concepto | Qué es | Dónde vive en el código |
|---|---|---|
| **Span** | Una ficha de una operación: nombre, inicio, duración, atributos | Lo crea tu código o la auto-instrumentación |
| **Trace** | El ID que agrupa los spans de una misma petición a través de todos los servicios | Se propaga solo, ver 4.4 |
| **Exporter** | El "correo" que envía spans/logs por red (protocolo OTLP) | `OTLPSpanExporter`, `OTLPLogExporter` |
| **Backend** | Quien guarda y muestra (Tempo/Loki/Grafana) | **No está en este repo** — ya lo tenías |

La idea clave: **tu código no sabe que existe Grafana**. Solo habla OTLP
hacia un endpoint. Cambiar de backend mañana = cambiar una variable de
entorno, sin tocar el código.

### 4.2 Identidad: el `Resource` y `service.name`

```python
resource = Resource(attributes={"service.name": SERVICE_NAME})
```

Todo span y todo log lleva esta etiqueta de identidad. Es **la** etiqueta
más importante de OpenTelemetry: sin ella los datos de tus servicios llegan
como anónimos y no puedes filtrar por servicio en Grafana. En el
`docker-compose.yml` se define por servicio:
`OTEL_SERVICE_NAME: payments` / `inventory` / `load-generator`.

### 4.3 El pipeline de una traza: 4 piezas en cadena

```python
trace_provider = TracerProvider(resource=resource)                       # 1. QUIEN recolecta
trace_provider.add_span_processor(                                       # 2. COMO se procesa
    BatchSpanProcessor(                                                  #    (en lotes, cada ~5s)
        OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)          # 3. A DONDE se envia
    )
)
trace.set_tracer_provider(trace_provider)                                # 4. Se registra como global
tracer = trace.get_tracer(__name__)                                      #    y se obtiene el tracer
```

Lectura detallada:

1. **`TracerProvider`** — la fábrica de spans. Es quien "sabe" que estamos
   instrumentando. Solo debe crearse **una vez** por proceso.
2. **`BatchSpanProcessor`** — no envía cada span en el momento (sería lento);
   los acumula y envía en lotes cada pocos segundos. Por eso, justo después
   de hacer una petición, la traza tarda unos segundos en aparecer en
   Grafana: es normal.
3. **`OTLPSpanExporter`** — el correo. `endpoint` apunta al recolector OTLP
   del LGTM (`192.168.3.60:4317`, gRPC sin TLS porque es red interna). La
   variable `OTEL_EXPORTER_OTLP_ENDPOINT` del compose lo hice configurable.
4. **`set_tracer_provider` / `get_tracer`** — registrar el provider como
   global y obtener un `tracer` para crear spans manuales.

### 4.4 La magia: propagación de contexto entre servicios

Cuando `payments` llama a `inventory` con `requests.get(...)`, pasa esto
invisiblemente:

```
GET /items/SKU-1001 HTTP/1.1
traceparent: 00-<trace_id>-<span_id>-01     <-- header inyectado automaticamente
```

Gracias a eso, el span que `inventory` crea para atender la petición queda
**enganchado como hijo** del span de payments, con el mismo `trace_id`.
Nadie escribió código para esto: lo hace
`RequestsInstrumentor().instrument()` (lado cliente) y
`FlaskInstrumentor().instrument_app(app)` (lado servidor).

**Sin propagación solo tendrías trazas rotas de un servicio cada una. Con
propagación, Tempo arma la película completa:**
`load-generator → payments → inventory`.

### 4.5 Auto-instrumentación vs. spans manuales

```python
FlaskInstrumentor().instrument_app(app)    # spans de ENTRADA: cada peticion HTTP
RequestsInstrumentor().instrument()        # spans de SALIDA: cada llamada requests
```

Estas dos líneas capturan **todo el tráfico HTTP sin escribir más nada**
(span `POST /payments`, span `GET`, métodos, códigos de estado, duración).
Cubre el 80% de lo que te interesa medir.

Lo que HTTP no ve, lo mides tú con spans manuales:

```python
with tracer.start_as_current_span("payments.process_payment") as span:
    span.set_attribute("payment.item_id", item_id)   # metadata clave=valor
    span.set_attribute("payment.quantity", quantity)

    with tracer.start_as_current_span("payments.charge"):   # span hijo (anidado)
        time.sleep(random.uniform(0.05, 0.25))              # latencia simulada
```

Regla práctica: **span manual para tu lógica de negocio** (lo que te importa
medir: "cuánto tarda cobrar"), auto-instrumentación para el plumbing HTTP.

Dos extras útiles dentro de un span:
- **`set_attribute(k, v)`** — datos que luego ves como columnas en Grafana
  (`payment.amount`, `inventory.stock`...).
- **`add_event(nombre, {...})` y `record_exception(exc)`** — momentos
  puntuales ("insufficient_stock") o errores con stack trace.

### 4.6 El tercer pilar: logs con trace_id (lo más reciente)

Las trazas cuentan la historia estructurada; los logs, los comentarios de
cada paso. La configuración es un espejo del pipeline de trazas:

```python
logs_provider = LoggerProvider(resource=resource)                  # misma identidad
set_logger_provider(logs_provider)
logs_provider.add_log_record_processor(
    BatchLogRecordProcessor(
        OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)     # mismo endpoint
    )
)
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(
    LoggingHandler(logger_provider=logs_provider)                  # puente con logging de Python
)
logger = logging.getLogger(SERVICE_NAME)
```

`LoggingHandler` envuelve el módulo `logging` estándar de Python. El truco
está en **dónde** emites el mensaje:

```python
with tracer.start_as_current_span("payments.process_payment"):
    logger.info("pago recibido", extra={"item_id": item_id})
    # ^ este log viaja con trace_id y span_id adjuntos automaticamente
```

El resultado en Loki: cada línea de log lleva el `trace_id` del span activo
en ese instante. En Grafana puedes abrir una traza en Tempo, hacer clic en
el menú de un span → **"View logs"** y ver **solo los logs de ese momento
de esa operación**, aunque el servicio genere miles de líneas por minuto.

Y en sentido inverso: en Explore → Loki, cada línea con `trace_id` tiene un
botón para saltar a su traza en Tempo. Esa ida y vuelta es la correlación
que hace que "trazas + logs" juntos valgan mucho más que cada uno por
separado.

> Nota sobre el tercer pilar que **no** usamos directamente: las **métricas**
> (`traces_spanmetrics_*`) las genera el *metrics-generator de Tempo*
> leyendo nuestros spans — no hace falta instrumentarlas en el código.

### 4.7 Los tres pilares, resumen

| Pilar | ¿Quién lo produce? | ¿Dónde aterriza? | ¿Dónde se ve? |
|---|---|---|---|
| Trazas | `tracer.start_as_current_span` + auto-instrumentación | Tempo | Explore → Tempo |
| Logs | `logger.info(...)` dentro de spans | Loki | Explore → Loki (o desde un span) |
| Métricas | metrics-generator de Tempo (deriva de trazas) | Prometheus | Dashboard RED |

### 4.8 Cardinalidad: por qué nuestras etiquetas son sanas

Cardinalidad = cuántas series distintas genera una métrica = producto de
sus etiquetas. Nuestro caso:

```
traces_spanmetrics_calls_total
  service(3) × span_name(~8) × span_kind(3) × status_code(2) ≈ 144 series máximo
```

Acotado y diminuto. El peligro en producción son etiquetas con valores
ilimitados (`user_id`, `trace_id`, URL sin normalizar): explotan la memoria
de Prometheus. Por eso la regla de oro: **las trazas pueden tener
cardinalidad infinita; las métricas no deben tenerla.** Los IDs viven
dentro de los spans/logs, jamás como etiquetas de métricas.

---

## 5. Ver todo en Grafana

Acceso: **http://192.168.3.60:3029** (usuario `admin`).

### Trazas
1. **Explore** → datasource **Tempo**
2. Query: `service.name = "payments"` → **Run query**
3. Clic en una traza: ves el árbol completo
   `load-generator → payments → inventory` con duraciones y atributos.

### Logs
1. **Explore** → datasource **Loki**
2. Query: `{service_name="payments"}` → verás las líneas con sus `trace_id`.

### Correlación traza ↔ logs
Desde una traza abierta en Tempo: menú de cualquier span → **View logs**
(muestra solo los logs de ese span). O desde Loki: clic en el `trace_id`
de una línea → salta a la traza.

### Dashboard RED
**http://192.168.3.60:3029/d/otel-demo-red** — rate, errores y latencia
(p50/p95) por servicio, derivados de las trazas. Se auto-provisiona desde
este repo al arrancar el contenedor LGTM.

![Traza distribuida de ejemplo](docs/images/example-trace.png)

---

## 6. Operación

### Levantar / bajar la demo (en el servidor)

```bash
cd /home/devtao/otel_python_demo
docker compose up -d --build     # levantar (recompila si el codigo cambio)
docker compose logs -f payments  # ver logs en vivo
docker compose down              # bajar
```

> El proyecto vive en `/home/devtao/otel_python_demo` y los datos de LGTM
> en `/home/devtao/otel-lgtm_data` — **ambos persisten reinicios**. Nada
> importante debe vivir en `/tmp` (el servidor lo vacía al reiniciar).

### Probar manualmente

```bash
curl http://192.168.3.60:5000/health          # payments
curl http://192.168.3.60:5001/items           # catalogo
curl -X POST http://192.168.3.60:5000/payments \
  -H "Content-Type: application/json" \
  -d '{"item_id": "SKU-1001", "quantity": 2}' # pago (genera traza completa)
```

### Re-provisionar el dashboard (respaldo del auto-provisioning)

```bash
cd /home/devtao/otel_python_demo
GRAFANA_PASSWORD='tu-clave' ./scripts/provision_dashboard.sh
```

---

## 7. Estructura del repositorio

```
otel_python_demo/
├── docker-compose.yml            # Orquestacion de los 3 servicios
├── README.md                     # Este documento
├── dashboards/
│   └── red-dashboard.json        # Dashboard RED (fuente para provisioning)
├── grafana/
│   └── provisioning/dashboards/  # Provisioning: provider.yaml + dashboard
│                                 # (montado en el contenedor LGTM)
├── scripts/
│   └── provision_dashboard.sh    # Respaldo manual de provisionamiento
├── docs/
│   ├── ARCHITECTURE.md           # Arquitectura detallada
│   ├── diagrama-arquitectura.drawio
│   └── images/
│       ├── architecture.png
│       └── example-trace.png     # Traza real renderizada (waterfall)
├── inventory/                    # Servicio Flask: catalogo + stock
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
├── payments/                     # Servicio Flask: cobros (llama a inventory)
│   ├── Dockerfile
│   ├── app.py
│   └── requirements.txt
└── load-generator/               # Script de carga continua
    ├── Dockerfile
    ├── generator.py
    └── requirements.txt
```
