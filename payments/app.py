"""Servicio de pagos - Flask + OpenTelemetry.

Recibe pagos y consulta el servicio de inventory (via HTTP) para
validar que el producto existe y tiene stock. La llamada saliente
(requests) queda instrumentada y se propaga el contexto de traza
(traceparent), asi Tempo une toda la cadena en una sola traza.
"""
import os
import random
import time
import uuid

import requests
from flask import Flask, jsonify, request

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "192.168.3.60:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "payments")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://inventory:5001")

resource = Resource(attributes={"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

app = Flask(__name__)
FlaskInstrumentor().instrument_app(app)
RequestsInstrumentor().instrument()  # instrumenta todas las llamadas `requests`

PAYMENTS_DB = []  # "base de datos" en memoria


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": SERVICE_NAME}), 200


@app.get("/payments")
def list_payments():
    return jsonify({"payments": PAYMENTS_DB, "count": len(PAYMENTS_DB)}), 200


@app.post("/payments")
def create_payment():
    payload = request.get_json(force=True, silent=True) or {}
    item_id = payload.get("item_id", "SKU-1002")
    quantity = int(payload.get("quantity", 1))

    with tracer.start_as_current_span("payments.process_payment") as span:
        span.set_attribute("payment.item_id", item_id)
        span.set_attribute("payment.quantity", quantity)

        # 1) Consulta al servicio inventory (span hijo automatico por
        #    opentelemetry-instrumentation-requests + propagacion de contexto)
        resp = requests.get(f"{INVENTORY_URL}/items/{item_id}", timeout=5)
        if resp.status_code != 200:
            span.set_attribute("payment.approved", False)
            span.add_event("inventory_lookup_failed", {"http.status_code": resp.status_code})
            return jsonify({"error": "producto invalido", "detail": resp.json()}), 400

        item = resp.json()
        if item["stock"] < quantity:
            span.set_attribute("payment.approved", False)
            span.add_event("insufficient_stock", {"stock": item["stock"]})
            return jsonify({"error": "stock insuficiente", "stock": item["stock"]}), 409

        # 2) Simula procesamiento del pago (pasarela)
        with tracer.start_as_current_span("payments.charge") as charge_span:
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
        return jsonify(payment), 201


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
