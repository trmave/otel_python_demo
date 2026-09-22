"""Servicio de inventario - Flask + OpenTelemetry.

Expone un catalogo de productos y verifica disponibilidad de stock.
Todas las peticiones HTTP son instrumentadas automaticamente y las
trazas se exportan por OTLP gRPC al backend LGTM (Tempo).
"""
import os
import random
import time

from flask import Flask, jsonify

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "192.168.3.60:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "inventory")

resource = Resource(attributes={"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)

# Catalogo "en memoria" del inventario
ITEMS = {
    "SKU-1001": {"name": "Laptop 14\"", "price": 899.99, "stock": 25},
    "SKU-1002": {"name": "Mouse inalambrico", "price": 19.99, "stock": 340},
    "SKU-1003": {"name": "Teclado mecanico", "price": 79.50, "stock": 120},
    "SKU-1004": {"name": "Monitor 27\"", "price": 249.00, "stock": 0},
    "SKU-1005": {"name": "Hub USB-C", "price": 34.90, "stock": 75},
}


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME}), 200


@app.get("/items")
def list_items():
    with tracer.start_as_current_span("inventory.query_catalog") as span:
        # Simula latencia de consulta a base de datos
        time.sleep(random.uniform(0.01, 0.08))
        span.set_attribute("inventory.item_count", len(ITEMS))
        return jsonify({"items": [{"sku": k, **v} for k, v in ITEMS.items()]}), 200


@app.get("/items/<item_id>")
def get_item(item_id):
    with tracer.start_as_current_span("inventory.check_stock") as span:
        span.set_attribute("inventory.sku", item_id)
        # Simula latencia de lectura a BD
        time.sleep(random.uniform(0.01, 0.06))
        item = ITEMS.get(item_id)
        if item is None:
            span.set_attribute("inventory.found", False)
            return jsonify({"error": f"item {item_id} no encontrado"}), 404
        span.set_attribute("inventory.found", True)
        span.set_attribute("inventory.stock", item["stock"])
        return jsonify({"sku": item_id, **item}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
