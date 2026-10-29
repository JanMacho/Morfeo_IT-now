#!/bin/sh
# Descarga de MinIO/S3 los "trafico/<replica>.json" de todas las réplicas y los
# concatena en un solo fichero, en formato JSON Lines que ya lee
# `log_analyzer` / `morfeo generate`. No hay cabecera ni orden que respetar:
# cada línea es independiente, así que concatenar basta.
#
# Uso:
#   MINIO_ENDPOINT=http://localhost:9000 \
#   MINIO_BUCKET=trafico-logs \
#   MINIO_ACCESS_KEY=... MINIO_SECRET_KEY=... \
#   ./fetch-aggregated-log.sh trafico-aggregated.json

set -eu

OUT="${1:-trafico-aggregated.json}"
ALIAS=logs
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

mc alias set "$ALIAS" "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null

mc cp -q --recursive "$ALIAS/$MINIO_BUCKET/trafico/" "$TMPDIR/"

: > "$OUT"
for f in "$TMPDIR"/*.json; do
  [ -e "$f" ] || continue
  cat "$f" >> "$OUT"
done

echo "fetch-aggregated-log: $(wc -l < "$OUT") líneas de $(ls "$TMPDIR"/*.json 2>/dev/null | wc -l) réplicas -> $OUT"
