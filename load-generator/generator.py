"""Generador de carga - Python + OpenTelemetry.

Ejecuta un bucle infinito que dispara pagos aleatorios contra el
servicio `payments`, generando trafico continuo para observar
trazas distribuidas de extremo a extremo en Grafana/Tempo:
    load-generator -> payments -> inventory

Tambien emite logs OTLP con trace_id, para ver en Grafana la iteracion
completa: traza + logs del mismo contexto.
"""
import logging
import os
import random
import time

import requests

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "192.168.3.60:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "load-generator")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:5000")
INTERVAL = float(os.getenv("LOAD_INTERVAL_SECONDS", "1.0"))

ITEMS = ["SKU-1001", "SKU-1002", "SKU-1003", "SKU-1004", "SKU-1005", "SKU-9999"]

# Trazas
resource = Resource(attributes={"service.name": SERVICE_NAME})
trace_provider = TracerProvider(resource=resource)
trace_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
trace.set_tracer_provider(trace_provider)
tracer = trace.get_tracer(__name__)

# Logs
logs_provider = LoggerProvider(resource=resource)
set_logger_provider(logs_provider)
logs_provider.add_log_record_processor(
    BatchLogRecordProcessor(OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True))
)
logging.basicConfig(level=logging.INFO)
logging.getLogger().addHandler(LoggingHandler(logger_provider=logs_provider))
logger = logging.getLogger(SERVICE_NAME)

RequestsInstrumentor().instrument()

print(f"[load-generator] objetivo: {PAYMENTS_URL} | intervalo: {INTERVAL}s", flush=True)

while True:
    with tracer.start_as_current_span("loadgenerator.iteration") as span:
        item = random.choice(ITEMS)
        qty = random.randint(1, 3)
        span.set_attribute("load.item_id", item)
        span.set_attribute("load.quantity", qty)
        logger.info("nueva iteracion", extra={"item_id": item, "quantity": qty})
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
            if r.status_code >= 400:
                logger.warning("respuesta con error", extra={"http_status": r.status_code, "item_id": item})
        except requests.RequestException as exc:
            span.set_attribute("load.error", str(exc))
            span.record_exception(exc)
            logger.error("fallo de peticion", extra={"error": str(exc)})
    time.sleep(INTERVAL)
