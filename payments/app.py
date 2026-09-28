"""Servicio de pagos - Flask + OpenTelemetry.

Recibe pagos y consulta el servicio de inventory (via HTTP) para
validar que el producto existe y tiene stock. La llamada saliente
(requests) queda instrumentada y se propaga el contexto de traza
(traceparent), asi Tempo une toda la cadena en una sola traza.

Tambien exporta LOGS por OTLP: cada mensaje de logging emitido DENTRO
de un span lleva automaticamente el trace_id/span_id activo, y Grafana
puede saltar de un span de la traza directamente a sus logs en Loki.
"""
import logging
import os
import random
import time
import uuid

import requests
from flask import Flask, jsonify, request

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "192.168.3.60:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payments")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory:5001")

# ---------------------------------------------------------------------------
# TRAZAS: quien soy, a donde exporto y que capturo automaticamente
# ---------------------------------------------------------------------------
resource = Resource(attributes={"service.name": SERVICE_NAME})

trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer(__name__)

# ---------------------------------------------------------------------------
# LOGS: el SDK de logs toma el modulo `logging` de Python y adjunta
# trace_id/span_id a cada registro hecho dentro de un span activo.
# ---------------------------------------------------------------------------
logs_provider = LoggerProvider(resource=resource)
set_logger_provider(logs_provider)
logs_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(LoggingHandler(logger_provider=logs_provider))
logger = logging.getLogger(SERVICE_NAME)

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()  # instrumenta todas las llamadas `requests`

PAYMENTS_DB = []  # "base de datos" en memoria


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME}), 200


@app.get("/payments")
def list_payments():
    logger.info("listando pagos", extra={"payments.count": len(PAYMENTS_DB)})
    return jsonify({"payments": PAYMENTS_DB, "count": len(PAYMENTS_DB)}), 200


@app.post("/payments")
def create_payment():
    payload = request.get_json(force=True, silent=True) or {}
    item_id = payload.get("item_id", "SKU-1002")
    quantity = int(payload.get("quantity", 1))

    with tracer.start_as_current_span("payments.process_payment") as span:
        span.set_attribute("payment.item_id", item_id)
        span.set_attribute("payment.quantity", quantity)
        logger.info("pago recibido", extra={"item_id": item_id, "quantity": quantity})

        # 1) Consulta al servicio inventory (span hijo automatico por
        #    opentelemetry-instrumentation-requests + propagacion de contexto)
        logger.info("consultando inventory", extra={"inventory.url": f"{INVENTORY_URL}/items/{item_id}"})
        resp = requests.get(f"{INVENTORY_URL}/items/{item_id}", timeout=5)
        if resp.status_code != 200:
            span.set_attribute("payment.approved", False)
            span.add_event("inventory_lookup_failed", {"http.status_code": resp.status_code})
            logger.warning("producto invalido", extra={"item_id": item_id, "http.status": resp.status_code})
            return jsonify({"error": "producto invalido", "detail": resp.json()}), 400

        item = resp.json()
        if item["stock"] < quantity:
            span.set_attribute("payment.approved", False)
            span.add_event("insufficient_stock", {"stock": item["stock"]})
            logger.warning("stock insuficiente", extra={"item_id": item_id, "stock": item["stock"], "quantity": quantity})
            return jsonify({"error": "stock insuficiente", "stock": item["stock"]}), 409

        # 2) Simula procesamiento del pago (pasarela)
        with tracer.start_as_current_span("payments.charge") as charge_span:
            logger.info("procesando cargo con pasarela")
            time.sleep(random.uniform(0.05, 0.25))
            charge_span.set_attribute("payment.amount", item["price"] * quantity)

        payment = {
            "payment_id": str(uuid.uuid4()),
            "item_id": item_id,
            "quantity": quantity,
            "amount": round(item["price"] * quantity, 2),
            "status": "approved",
        }
        PAYMENTS_DB.append(payment)
        span.set_attribute("payment.approved", True)
        span.set_attribute("payment.id", payment["payment_id"])
        logger.info("pago aprobado", extra={"payment_id": payment["payment_id"], "amount": payment["amount"]})
        return jsonify(payment), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
