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
cd /tmp/otel_python_demo
docker compose up -d --build
docker compose logs -f load-generator
```

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

## Detener

```bash
docker compose down
```
