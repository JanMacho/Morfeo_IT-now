#!/bin/sh
# Sube periódicamente una foto de TRAFFIC_LOG a MinIO/S3, bajo una clave de
# objeto propia de esta réplica (LOG_SHIP_KEY, por defecto el hostname del
# contenedor). Cada subida SOBREESCRIBE el mismo objeto: no acumula histórico,
# solo mantiene la última foto completa del log de esta réplica.
#
# En Kubernetes, el hostname del contenedor es el nombre del pod por
# defecto, así que cada réplica sube a una clave distinta sin configurar nada
# aparte.
#
# Variables de entorno esperadas (ver docker-compose.yml / manifiestos k8s):
#   MINIO_ENDPOINT, MINIO_BUCKET, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
#   TRAFFIC_LOG       ruta local del log de esta réplica (compartida con la app)
#   LOG_SHIP_KEY      clave de objeto; por defecto $(hostname)
#   LOG_SHIP_INTERVAL segundos entre subidas; por defecto 15

set -eu

KEY="${LOG_SHIP_KEY:-$(hostname)}"
INTERVAL="${LOG_SHIP_INTERVAL:-15}"
ALIAS=logs

mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

echo "ship-logs: subiendo $TRAFFIC_LOG a $ALIAS/$MINIO_BUCKET/trafico/$KEY.json cada ${INTERVAL}s"

while true; do
  if [ -f "$TRAFFIC_LOG" ]; then
    mc cp -q "$TRAFFIC_LOG" "$ALIAS/$MINIO_BUCKET/trafico/$KEY.json" || \
      echo "ship-logs: fallo al subir (se reintenta en el siguiente ciclo)" >&2
  fi
  sleep "$INTERVAL"
done
