# otel_python_demo

Demo de **trazas distribuidas con OpenTelemetry** usando Python + Flask.
Tres microservicios containerizados con Docker Compose generan trafico
continuo cuyas trazas se envian por OTLP a un backend **Grafana LGTM**
(Grafana + Tempo + Loki + Mimir) que corre en el mismo servidor.

## Arquitectura

```
load-generator  --HTTP-->  payments  --HTTP-->  inventory
     |                        |                    |
     +------------ OTLP gRPC (puerto 4317) --------+
                              |
                    Grafana LGTM (Tempo)
                              |
                    Grafana UI :3029
```

![Diagrama de arquitectura](docs/images/architecture.png)

Ver detalle en [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Diagrama editable en [docs/diagrama-arquitectura.drawio](docs/diagrama-arquitectura.drawio).

## Servicios

| Servicio        | Tecnologia | Puerto | Descripcion                                            |
| --------------- | ---------- | ------ | ------------------------------------------------------ |
| `inventory`     | Flask      | 5001   | Catalogo de productos y verificacion de stock          |
| `payments`      | Flask      | 5000   | Recibe pagos y consulta a `inventory` antes de cobrar  |
| `load-generator`| Python     | -      | Genera pagos aleatorios cada ~1s contra `payments`     |

## Requisitos previos

- Docker + Docker Compose en el servidor.
- Backend LGTM corriendo y publicando los puertos OTLP:
  - Grafana UI: `http://192.168.3.60:3029`
  - OTLP gRPC: `192.168.3.60:4317`
  - OTLP HTTP: `192.168.3.60:4318`

> La URL del exporter se configura con la variable
> `OTEL_EXPORTER_OTLP_ENDPOINT` en `docker-compose.yml`.

## Levantar la demo

```bash
cd /home/devtao/otel_python_demo
docker compose up -d --build
docker compose logs -f load-generator
```

> **Nota:** el proyecto vive en `/home/devtao/otel_python_demo` (persistente).
> No uses `/tmp` — el servidor lo vacia al reiniciar.

## Probar manualmente

```bash
# Health checks
curl http://192.168.3.60:5000/health
curl http://192.168.3.60:5001/health

# Listar catalogo
curl http://192.168.3.60:5001/items

# Crear un pago (payments -> inventory)
curl -X POST http://192.168.3.60:5000/payments \
  -H "Content-Type: application/json" \
  -d '{"item_id": "SKU-1001", "quantity": 2}'
```

## Ver las trazas en Grafana

1. Abrir `http://192.168.3.60:3029`
2. Ir a **Explore** → datasource **Tempo**
3. Buscar por nombre de servicio, por ejemplo `service.name = "payments"`
4. Abrir una traza: veras la cadena completa
   `load-generator → payments → inventory` con sus spans y atributos.

## Ejemplo de traza real

Traza distribuida capturada de Tempo mientras el generador de carga estaba
activo. Se aprecia la cadena completa: el span raiz `loadgenerator.iteration`,
la llamada HTTP a `payments`, la consulta a `inventory` (~17-19 ms) y el
procesamiento del cargo `payments.charge` (~195 ms de latencia simulada de
la pasarela de pago).

![Traza distribuida de ejemplo](docs/images/example-trace.png)

## Dashboard RED (Rate, Errors, Duration)

Existe un dashboard provisionado en Grafana con las métricas que el
**metrics-generator de Tempo** deriva de las trazas
(`traces_spanmetrics_*` en el datasource Prometheus):

- **Tráfico:** request rate por servicio (server spans) e iteraciones/s del generador
- **Errores:** spans con `status_code = ERROR` por servicio
- **Duración:** latencia p50/p95 por servicio y p95 por operación de `payments`
  (se distingue `POST /payments` de `payments.charge` y del `GET` a inventory)

URL: `http://192.168.3.60:3029/d/otel-demo-red`

El dashboard se provisiona **automaticamente al iniciar el contenedor LGTM**
desde [grafana/provisioning](grafana/provisioning/dashboards/provider.yaml)
(montado en `/otel-lgtm/grafana/conf/provisioning` dentro del contenedor),
asi que sobrevive recreaciones del contenedor. Los dashboards provisionados por archivo se
ven con un icono de "provisioned" y no se pueden editar desde la UI (cualquier
cambio se hace editando el JSON en el repo).

Respaldo manual por API (si el provisioning no estuviera montado):

```bash
cd /home/devtao/otel_python_demo
GRAFANA_PASSWORD='Cristina2019.' ./scripts/provision_dashboard.sh
```

El JSON del dashboard esta versionado en
[dashboards/red-dashboard.json](dashboards/red-dashboard.json).

## Despues de un reinicio del servidor

1. Los datos de LGTM persisten en `/home/devtao/otel-lgtm_data` (volumen
   montado en el contenedor) — dashboards, usuarios, trazas e historicos.
2. Los contenedores de la demo (`otel-*`) vuelven solos gracias a
   `restart: unless-stopped`. Si no:
   ```bash
   cd /home/devtao/otel_python_demo && docker compose up -d
   ```
3. Grafana queda en `http://192.168.3.60:3029` con el usuario `admin`.

## Detener

```bash
docker compose down
```
