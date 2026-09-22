"""Generador de carga - Python + OpenTelemetry.

Ejecuta un bucle infinito que dispara pagos aleatorios contra el
servicio `payments`, generando trafico continuo para observar
trazas distribuidas de extremo a extremo en Grafana/Tempo:
    load-generator -> payments -> inventory
"""
import os
import random
import time

import requests

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "192.168.3.60:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "load-generator")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:5000")
INTERVAL = float(os.getenv("LOAD_INTERVAL_SECONDS", "1.0"))

ITEMS = ["SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004", "SKU-1005", "SKU-9999"]

resource = Resource(attributes={"service.name": SERVICE_NAME})
provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

RequestsInstrumentor().instrument()

print(f"[load-generator] objetivo: {PAYMENTS_URL} | intervalo: {INTERVAL}s", flush=True)

while True:
    with tracer.start_as_current_span("loadgenerator.iteration") as span:
        item = random.choice(ITEMS)
        qty = random.randint(1, 3)
        span.set_attribute("load.item_id", item)
        span.set_attribute("load.quantity", qty)
        try:
            if random.random() < 0.15:
                # 15% de las veces solo lista pagos (lectura ligera)
                r = requests.get(f"{PAYMENTS_URL}/payments", timeout=5)
            else:
                r = requests.post(
                    f"{PAYMENTS_URL}/payments",
                    json={"item_id": item, "quantity": qty},
                    timeout=5,
                )
            span.set_attribute("load.http_status", r.status_code)
        except requests.RequestException as exc:
            span.set_attribute("load.error", str(exc))
            span.record_exception(exc)
    time.sleep(INTERVAL)
