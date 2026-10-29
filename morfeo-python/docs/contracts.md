# Contratos entre módulos

Este documento fija el formato de los ficheros intermedios. Cada módulo lee y
escribe solo lo que aparece aquí, de modo que se puede ejecutar por separado y
sustituir sin tocar los demás.

Reglas comunes:

- JSON en UTF-8, indentado a 2 espacios, sin `NaN` ni `Infinity` (se escriben
  como `null`). Se usa `morfeo.common.write_json`.
- Las salidas no llevan marcas de tiempo de ejecución ni rutas absolutas. Con
  las mismas entradas y la misma semilla, el fichero sale idéntico byte a byte.
- La semilla por defecto es `42` (`morfeo.common.DEFAULT_SEED`).
- Tiempos de espera y latencias en milisegundos. Duraciones de sesión en
  segundos. Timestamps en ISO 8601 UTC con microsegundos y sufijo `Z`.
- Identificador de endpoint: `"<MÉTODO> <plantilla>"`, por ejemplo
  `"GET /api/saldo/{id}"`. Es la clave que une los tres módulos.
- `schema_version` es `1` en todos los ficheros.

Flujo:

```
app.jar  --scan-->     endpoints.json
logs     --analyze-->  requests.csv + analysis.json    (usa endpoints.json si existe)
requests.csv --train--> profile.json + validation.json
profile.json + endpoints.json (+ requests.csv) --jmeter--> plan.jmx
```

## 1. endpoints.json (jar_scanner)

```json
{
  "schema_version": 1,
  "generator": "morfeo.jar_scanner",
  "jar": {
    "file": "app.jar",
    "sha256": "…",
    "layout": "spring-boot | war | plain",
    "main_class": "org.springframework.boot.loader.launch.JarLauncher",
    "start_class": "com.example.App",
    "class_versions": {"min": 65, "max": 65}
  },
  "context_path": "",
  "frameworks": ["spring"],
  "endpoints": [ Endpoint, … ],
  "warnings": ["texto", …]
}
```

`endpoints` va ordenado por `(path, method)`. Los `id` son únicos.

### Endpoint

```json
{
  "id": "POST /api/ingreso",
  "method": "POST",
  "path": "/api/ingreso",
  "path_regex": "^/api/ingreso$",
  "framework": "spring | javalin",
  "source": {"class": "com.example.BankController", "method": "ingreso", "descriptor": "(Lcom/example/dto/MovimientoRequest;)Lorg/springframework/http/ResponseEntity;"},
  "consumes": ["application/json"],
  "produces": [],
  "params": [ Param, … ],
  "body": null,
  "notes": []
}
```

- `method`: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS`, o `ANY`
  cuando `@RequestMapping` no fija método (Spring acepta cualquiera).
- `path`: empieza por `/`, sin barra final (salvo `/`), sin `//`. Incluye
  `context_path` si se aplicó. Las variables se escriben `{nombre}`; la forma
  `{nombre:regex}` de Spring se conserva en `path` y se traduce en `path_regex`.
- `path_regex`: expresión anclada. `{x}` pasa a `[^/]+`, `{x:re}` a `(?:re)`,
  `*` a `[^/]*`, `**` y `{*x}` a `.*`.

### Param

```json
{"name": "id", "in": "path | query | header | cookie | form", "java_type": "java.lang.String", "schema": TypeSchema, "required": true, "default": null}
```

### body

```json
{"java_type": "com.example.dto.MovimientoRequest", "content_type": "application/json", "schema": TypeSchema, "example": {"id": "string", "cantidad": 1.0}}
```

`example` es un valor JSON determinista construido a partir de `schema`.

### TypeSchema

Subconjunto de JSON Schema con campos propios:

| Campo | Uso |
|---|---|
| `type` | `string`, `integer`, `number`, `boolean`, `object`, `array`, `any` |
| `java_type` | nombre Java completo (`java.util.List<com.x.Item>`) |
| `format` | `int32`, `int64`, `decimal`, `float`, `double`, `date`, `date-time`, `time`, `uuid`, `binary`, `char` |
| `nullable` | `true` para `Optional<T>` y envoltorios (`Integer`, …) |
| `enum` | constantes de un `enum` Java |
| `items` | TypeSchema de los elementos de un array |
| `properties` | `{nombre_json: TypeSchema}` de un objeto |
| `required` | nombres con `@NotNull`, `@NotBlank` o `@NotEmpty` |
| `additional_properties` | TypeSchema de los valores de un `Map<String, V>` |
| `constraints` | `min`, `max`, `min_length`, `max_length`, `pattern`, `email`, `positive`, `negative`, `past`, `future`, `digits_integer`, `digits_fraction` |
| `cycle` | `true` si la recursión se cortó por un ciclo |
| `truncated` | `true` si se alcanzó la profundidad máxima |
| `unresolved` | `true` si la clase no está en el jar |

## 1b. trafico.json (backend, entrada de log_analyzer)

JSON Lines que escribe el backend Spring Boot: un objeto JSON por línea y por
petición, con estas siete claves en este orden. No es un único array JSON, para
que el backend pueda añadir líneas sin reescribir el fichero.

```
{"ts_end":"2026-10-01T09:25:34.548091Z","method":"POST","path":"/api/retirada","status":400,"latency_ms":0.734,"client_id":"user_002","session_id":"4ce89f0ec4024fdb9d390429d50db6a6"}
```

| Clave | Tipo | Descripción |
|---|---|---|
| `ts_end` | texto | **fin** de la petición, ISO 8601 en UTC con microsegundos |
| `method` | texto | método HTTP |
| `path` | texto | ruta tal como llegó, sin query string |
| `status` | entero | código HTTP |
| `latency_ms` | real | latencia en milisegundos, con 3 decimales |
| `client_id` | texto | `UNKNOWN` si no hay |
| `session_id` | texto | cookie `MORFEO_SID` del servidor, 32 caracteres hexadecimales |

- Los textos van escapados como JSON (`"`, `\` y caracteres de control), así que
  una ruta o un id con `|` o con un salto de línea no rompe la línea.
- `session_id` caduca tras 30 minutos sin peticiones. Una petición sin cookie
  recibe un id nuevo, así que un cliente sin gestión de cookies (por ejemplo
  JMeter sin *HTTP Cookie Manager*) genera una sesión por petición.
- `log_analyzer` lo detecta solo (formato `jsonl`): al ver la clave `ts_end`
  lee el timestamp como fin de la petición. No hace falta configuración.

**Formato anterior (`trafico.log`, `pipe`).** Hasta ahora el backend escribía
`TIMESTAMP | METODO | ENDPOINT | STATUS | TIEMPO_MS | ID_CLIENTE | ID_SESION`.
`log_analyzer` lo sigue leyendo (también con 6 columnas, sin `ID_SESION`, que se
agrupan por cliente con el corte por inactividad), pero el backend ya no lo
escribe. Si se fija `pipe_columns`, se exige ese número de columnas.

## 2. requests.csv (log_analyzer)

Una fila por petición, ordenada por `(session_id, seq)`. Columnas en este
orden (`morfeo.common.REQUESTS_COLUMNS`):

| Columna | Tipo | Descripción |
|---|---|---|
| `session_id` | texto | sesión del log, o `<cliente>#<n>` si se construye por inactividad |
| `client_id` | texto | vacío si no hay |
| `seq` | entero | posición dentro de la sesión, desde 0 |
| `ts_start` | ISO 8601 | inicio de la petición |
| `ts_end` | ISO 8601 | fin de la petición |
| `method` | texto | método HTTP en mayúsculas |
| `path` | texto | plantilla normalizada, por ejemplo `/api/saldo/{id}` |
| `raw_path` | texto | ruta original sin query string |
| `query` | texto | query string sin `?`, vacío si no hay |
| `endpoint` | texto | `"<method> <path>"` |
| `matched` | `true`/`false` | si la ruta casó con un endpoint de endpoints.json |
| `status` | entero | código HTTP |
| `latency_ms` | real | latencia |
| `think_time_ms` | real o vacío | `ts_start` de esta fila menos `ts_end` de la anterior de la misma sesión, recortado a 0. Vacío en `seq = 0` |
| `message` | texto | vacío si no hay |

Se lee y escribe con `morfeo.common.read_requests_csv` y
`write_requests_csv`.

## 3. analysis.json (log_analyzer)

```json
{
  "schema_version": 1,
  "generator": "morfeo.log_analyzer",
  "input": {"files": ["trafico.json"], "format": "jsonl | pipe", "timestamp_is": "end", "timezone": "UTC", "session_gap_s": 1800, "session_source": "session_id | client_gap | single"},
  "totals": {"requests": 0, "sessions": 0, "clients": 0, "start": "…Z", "end": "…Z", "duration_s": 0, "unmatched_requests": 0, "error_requests": 0, "server_error_requests": 0},
  "rps": {"mean": 0, "active_mean": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0},
  "requests_per_hour": {
    "by_hour_of_day": [{"hour": 0, "requests_total": 0, "days_observed": 0, "requests_mean_per_day": 0}],
    "series": [{"hour_start": "…Z", "requests": 0}]
  },
  "endpoint_mix": [{"endpoint": "GET /x", "method": "GET", "path": "/x", "matched": true, "count": 0, "share": 0, "error_rate": 0, "server_error_rate": 0, "latency_ms": {"mean": 0, "p50": 0, "p95": 0, "max": 0}}],
  "session_length": {"requests": Stats, "duration_s": Stats},
  "think_time_ms": {"count": 0, "negative_clipped": 0, "mean": 0, "p50": 0, "p90": 0, "p99": 0, "max": 0},
  "sequences": {"top_sequences": [{"sequence": ["GET /x"], "count": 0, "share": 0}], "top_bigrams": [{"from": "GET /x", "to": "POST /y", "count": 0}]},
  "unmatched_paths": [{"method": "GET", "path": "/raro", "count": 0}]
}
```

`Stats` es `{"count","mean","p50","p90","p99","max"}`. `rps` se calcula sobre
todos los segundos entre la primera y la última petición, incluidos los que no
tienen tráfico; `active_mean` solo sobre los segundos con tráfico. `hour` es la
hora local en `timezone`. Las listas `top_*` y `unmatched_paths` tienen como
máximo 20 elementos.

## 4. profile.json (traffic_model)

```json
{
  "schema_version": 1,
  "generator": "morfeo.traffic_model",
  "seed": 42,
  "input": {"requests": 0, "sessions": 0, "timezone": "UTC", "holdout": 0.0, "smoothing": 0.0, "min_state_samples": 30, "excluded_unmatched_requests": 0},
  "states": ["START", "GET /api/saldo/{id}", "…", "END"],
  "endpoints": {"GET /api/saldo/{id}": {"method": "GET", "path": "/api/saldo/{id}", "matched": true, "count": 0, "share": 0, "error_rate": 0, "server_error_rate": 0, "latency_ms": {"p50": 0, "p95": 0}}},
  "transition_matrix": {"START": {"GET /api/saldo/{id}": 1.0}},
  "transition_counts": {"START": {"GET /api/saldo/{id}": 10}},
  "think_time_ms": {"global": Dist, "by_state": {"GET /api/saldo/{id}": Dist}},
  "session_length": {"requests": Dist, "duration_s": Dist},
  "hourly_curve": {"timezone": "UTC", "days_observed": 1, "hours": [HourSlot × 24]},
  "concurrent_users": {"peak_hour": 10, "peak_recommended": 5, "daily_mean": 1.2},
  "baseline": {"endpoint_mix": {"GET /x": 0.5}, "think_time_ms": Dist, "session_length_requests": Dist}
}
```

- `states`: `START`, los endpoints ordenados alfabéticamente, `END`. Por
  defecto `morfeo train` y `morfeo generate` excluyen antes las peticiones con
  `matched = false` (404 a rutas inexistentes, bots) y recalculan `seq` y
  `think_time_ms` de las sesiones afectadas; el número va en
  `input.excluded_unmatched_requests`. `--keep-unmatched` las conserva.
- `transition_matrix[a][b]`: probabilidad de ir de `a` a `b`. Filas para todos
  los estados salvo `END`; columnas para todos salvo `START`. Cada fila suma 1
  (tolerancia 1e-9). Las entradas a 0 se omiten.
- `think_time_ms.by_state[a]`: tiempo de espera después de una petición a `a`
  (antes de la siguiente de la misma sesión). Solo para estados con al menos
  `min_state_samples` muestras; si falta, se usa `global`.
- `Dist`: `{"count","mean","std","min","p50","p90","p95","p99","max","quantiles","lognormal"}`.
  `quantiles` son 101 valores (percentiles 0 a 100). `lognormal` es
  `{"mu","sigma"}` ajustado sobre los valores positivos, o `null`.
- `HourSlot`: `{"hour","sessions_per_hour","requests_per_hour","rps","share_of_daily_requests","mean_session_duration_s","concurrent_users"}`
  con `concurrent_users = {"little","observed_mean","observed_p95","observed_max","recommended"}`.
  Las medias son por día observado. `little` es la ley de Little
  (llegadas de sesiones por segundo por duración media de sesión).
  `observed_*` salen de contar cada segundo las sesiones activas en ese
  instante (el paso crece en logs muy largos, con un máximo de 5 millones de
  instantes).
  `recommended = max(1, ceil(observed_p95))` si la hora tiene tráfico, 0 si no.
- `baseline`: modelo sin memoria (endpoints independientes con la mezcla
  observada), que sirve de referencia para medir lo que aporta la cadena de
  Markov.

## 5. validation.json (traffic_model)

```json
{
  "schema_version": 1,
  "generator": "morfeo.traffic_model.validate",
  "seed": 42,
  "reference": "in-sample | holdout",
  "n_real_sessions": 0,
  "n_synthetic_sessions": 0,
  "think_time_ks": {"statistic": 0, "p_value": 0, "n_real": 0, "n_synthetic": 0},
  "session_length_ks": {"statistic": 0, "p_value": 0, "n_real": 0, "n_synthetic": 0},
  "transition_diff": {"markov": Diff, "baseline": Diff},
  "endpoint_mix_tv": {"markov": 0, "baseline": 0},
  "thresholds": {"ks_statistic": 0.1, "weighted_tv": 0.05},
  "verdict": {"think_time_ok": true, "session_length_ok": true, "transitions_ok": true}
}
```

`Diff` es `{"max_abs","mean_abs","frobenius","weighted_tv"}`, comparando la
matriz real con la reestimada sobre las sesiones sintéticas. `weighted_tv` es
la distancia de variación total de cada fila, ponderada por las visitas reales
a esa fila.

## 6. Interfaz de CLI de cada módulo

Cada paquete (`jar_scanner`, `log_analyzer`, `traffic_model`) tiene un módulo
`cli.py` con:

```python
def add_arguments(parser: argparse.ArgumentParser) -> None: ...
def run(args: argparse.Namespace) -> int: ...
```

y un `__main__.py` que permite `python -m morfeo.<paquete> …`. `morfeo.cli`
monta los subcomandos a partir de estas dos funciones.
