# MORFEO: bloque de procesamiento

MORFEO genera pruebas de carga a partir de una aplicación y de su uso real.
Este paquete es el bloque de procesamiento. Recibe el `.jar` de la aplicación
y sus logs de uso, y entrega:

- `endpoints.json`: los endpoints HTTP que hay en el `.jar`, con parámetros y
  un ejemplo de cuerpo para cada uno.
- `profile.json`: cómo usan la aplicación los usuarios. Incluye en qué orden
  llaman a los endpoints, cuánto esperan entre peticiones, cómo cambia la
  carga a lo largo del día y cuántos usuarios hay a la vez.
- `plan.jmx`: el plan de JMeter que reproduce ese comportamiento contra la
  API (`morfeo jmeter`, que `morfeo generate` ejecuta al final). Cómo se usa
  y qué hace: [docs/jmeter.md](docs/jmeter.md). La nota
  [docs/jmeter-handoff.md](docs/jmeter-handoff.md) explica de qué campo sale
  cada parte del plan.

Proyecto PAE de la UPC ETSETB, propuesto por ITnow.

## Índice

1. [Instalación](#1-instalación)
2. [Ejemplo completo en tres comandos](#2-ejemplo-completo-en-tres-comandos)
3. [Paso a paso](#3-paso-a-paso)
4. [Formatos de log](#4-formatos-de-log)
5. [Ficheros que se generan](#5-ficheros-que-se-generan)
6. [Cómo leer el informe de validación](#6-cómo-leer-el-informe-de-validación)
7. [Decisiones de diseño](#7-decisiones-de-diseño)
8. [Limitaciones conocidas](#8-limitaciones-conocidas)
9. [Tests](#9-tests)
10. [Estructura del código](#10-estructura-del-código)

## 1. Instalación

Hace falta Python 3.11 o superior. Las dependencias son pandas y numpy. No
hace falta Java para usar MORFEO; solo para ejecutar los tests del escáner.

```bash
cd morfeo-python
python -m venv .venv
source .venv/bin/activate          # en Windows: .venv\Scripts\activate
pip install -e ".[dev]"
morfeo --help
```

```
    scan        descubre endpoints en un .jar
    analyze     analiza logs de uso
    train       entrena el modelo de tráfico
    validate    valida un profile.json
    synth-logs  genera logs sintéticos
    jmeter      genera el plan de JMeter (.jmx)
    generate    encadena scan, analyze y train
```

Cada subcomando también se puede ejecutar como módulo, por ejemplo
`python -m morfeo.jar_scanner app.jar`.

## 2. Ejemplo completo en tres comandos

Con el jar del backend del equipo y una semana de logs sintéticos (todavía no
hay logs reales suficientes; ver [synth-logs](#synth-logs-logs-de-prueba)):

```bash
morfeo synth-logs --days 7 -o logs/trafico.json
morfeo generate --jar ../backend-java/target/bank-api-stress.jar --logs logs --out-dir build --tz Europe/Madrid --holdout 0.3
ls build
```

Salida:

```
scan: 4 endpoints en build/endpoints.json
analyze: 10173 peticiones, 2999 sesiones, 199 sin endpoint, en build/analysis.json
train: 4 endpoints (199 peticiones sin endpoint excluidas) en build/profile.json
Validación del modelo de tráfico
  Referencia: sesiones reservadas (holdout), 840 sesiones reales y 10000 sintéticas (semilla 42)
  Tiempo de espera, KS: D = 0.016, p = 0.7309 (2034 reales, 25861 sintéticos)
  Peticiones por sesión, KS: D = 0.030, p = 0.4755
  Matriz de transición, Markov: TV ponderada = 0.024, diferencia máxima = 0.070
  Matriz de transición, base sin memoria: TV ponderada = 0.284, diferencia máxima = 0.310
  Mezcla de endpoints, TV: Markov 0.006, base 0.005
  Umbrales: D <= 0.1, TV ponderada <= 0.05
  Veredicto: tiempos de espera correcto, longitud de sesión correcto, transiciones correcto

analysis.json  endpoints.json  profile.json  requests.csv  validation.json
```

Los comandos van en una sola línea. Si los partís en varias, en Linux y Mac se termina la línea con `\`, pero en PowerShell se usa `` ` `` (acento grave). Un `\` suelto en PowerShell es la raíz del disco.

`--tz` es la zona horaria en la que se calcula la curva por hora. Los logs
suelen estar en UTC y los usuarios viven en hora local. `--holdout 0.3`
reserva el 30 % de las sesiones más recientes para validar el modelo con
tráfico que no ha visto al entrenar.

## 3. Paso a paso

`generate` encadena los pasos, pero cada uno funciona por separado con sus
ficheros intermedios. Así se puede repetir un paso sin rehacer los demás, o
sustituir un módulo.

```
app.jar  --scan-->     endpoints.json
logs     --analyze-->  requests.csv + analysis.json    (usa endpoints.json)
requests.csv --train--> profile.json + validation.json
```

### scan: endpoints del .jar

```bash
morfeo scan app.jar -o endpoints.json
```

```
4 endpoints (4 spring) en endpoints.json
```

El escáner lee el bytecode sin ejecutar la aplicación. Qué reconoce:

- Aplicaciones Spring Boot (fat jar con `BOOT-INF/classes`), `.war` y jars
  normales o sombreados.
- Spring Web MVC:
  - `@RestController`, `@Controller`, `@RequestMapping` y `@GetMapping`,
    `@PostMapping`, `@PutMapping`, `@DeleteMapping` y `@PatchMapping`.
  - Parámetros con `@PathVariable`, `@RequestParam`, `@RequestHeader`,
    `@CookieValue`, `@RequestBody` y `@ModelAttribute`, y formularios sin
    anotar.
  - Anotaciones heredadas de interfaces, que es como genera el código
    OpenAPI generator.
  - El context-path de `application.properties` o `application.yml`.
- Javalin (la primera versión del backend del equipo lo usaba): rutas `app.get("/ruta", ...)`,
  `ApiBuilder`, y `ctx.bodyAsClass(...)` dentro del handler.
- DTO:
  - Campos, records y herencia.
  - Anotaciones de Jackson (`@JsonProperty`, `@JsonIgnore`) y de validación
    (`@NotBlank`, `@Size`, `@Min`, `@Pattern`...).
  - Un ejemplo de cuerpo que cumple esas restricciones.

Probado con el jar del backend (4 de 4 endpoints) y con
[Spring PetClinic](https://github.com/spring-projects/spring-petclinic)
(17 de 17 mappings).

Opciones útiles: `--package com.miempresa` para limitar el escaneo a un
paquete, `--no-context-path`, `--no-javalin` e `--include-lib "modulo-*.jar"`
para escanear también un jar anidado del propio proyecto.

### analyze: logs de uso

```bash
morfeo analyze logs/ --endpoints endpoints.json --tz Europe/Madrid -o out
```

```
Peticiones: 10173 en 2999 sesiones de 373 clientes
Periodo: 2026-09-20T22:18:33.736092Z a 2026-09-27T21:29:25.483561Z (601851.747469 s)
Peticiones por segundo: media 0.016903, media en segundos con tráfico 1.014864, pico 3
Endpoints más frecuentes:
  GET /api/saldo/{id}: 5449 (53.6 %)
  POST /api/ingreso: 2298 (22.6 %)
  POST /api/retirada: 2004 (19.7 %)
  POST /api/usuario/crear: 223 (2.2 %)
  GET /api/v1/saldo/{id}: 24 (0.2 %)
Rutas sin endpoint: 199 peticiones en 10 rutas distintas
```

Qué hace:

1. Lee los logs (ficheros o carpetas, también `.gz`) y detecta el formato
   ([sección 4](#4-formatos-de-log)).
2. Normaliza las rutas: `/api/saldo/user_001` pasa a `/api/saldo/{id}`.
   Primero busca la plantilla en `endpoints.json`; si no casa ninguna, aplica
   una regla (números, UUID y textos con letras y dígitos pasan a `{id}`).
3. Agrupa las peticiones en sesiones. Si el log trae `session_id`, lo usa. Si
   no, agrupa por cliente y corta la sesión tras 30 minutos sin actividad
   (`--session-gap` en segundos).
4. Calcula el tiempo de espera entre peticiones de una misma sesión: inicio
   de la siguiente menos fin de la anterior.
5. Calcula peticiones por segundo y por hora, mezcla de endpoints, longitud
   de sesión, esperas y secuencias más frecuentes.

### train: modelo de tráfico

```bash
morfeo train out/requests.csv -o profile.json --tz Europe/Madrid --holdout 0.3
```

Entrena dos modelos:

- **Base:** solo estadística. Cada petición se elige según el peso de cada
  endpoint, sin tener en cuenta la anterior.
- **Cadena de Markov:** probabilidad de pasar de cada endpoint al siguiente,
  con un estado de inicio (`START`) y otro de fin (`END`) de sesión.

Además calcula la curva de carga por hora y los usuarios concurrentes. Al
terminar valida el modelo y escribe `validation.json` junto a `profile.json`
([sección 6](#6-cómo-leer-el-informe-de-validación)).

Por defecto se quitan las peticiones a rutas que no están en el jar (sobre
todo 404 de bots, como `/.env` o `/wp-login.php`), porque el plan de carga
debe reproducir a los usuarios de la aplicación. `--keep-unmatched` las
conserva.

Otras opciones: `--smoothing 1` suma 1 a cada transición posible, para que
el modelo pueda generar transiciones que no aparecen en los logs;
`--min-state-samples 30` fija las muestras mínimas para calcular la espera de
cada endpoint por separado; `--no-validate`.

### validate: validar un modelo existente

```bash
morfeo validate profile.json otros/requests.csv -o validation.json
```

Sirve para comprobar un modelo con otros logs, por ejemplo de otra semana.

### jmeter: plan de JMeter

```bash
morfeo jmeter build/profile.json build/endpoints.json --requests build/requests.csv -o plan.jmx
```

Genera el plan `.jmx` que reproduce el tráfico aprendido: usuarios de los logs
con su frecuencia, orden de peticiones por la cadena de Markov, esperas del
perfil, importes según el esquema y el saldo visto, y la carga por hora o en
escalones. `morfeo generate` lo ejecuta al final (`--no-jmx` lo omite). Todas
las opciones y el detalle están en [docs/jmeter.md](docs/jmeter.md).

### synth-logs: logs de prueba

```bash
morfeo synth-logs --days 7 --sessions-per-day 400 -o logs/trafico.json --ground-truth logs/verdad.json
```

Genera logs con el mismo formato que el `trafico.json` del backend (JSON Lines con `ts_end`,
`method`, `path`, `status`, `latency_ms`, `client_id` y `session_id`; cada sesión lleva un id
de 32 caracteres hexadecimales, como la cookie del servidor). Con `--format pipe` sale el formato
antiguo del backend y con `--format jsonl` un JSON Lines genérico. Como no habrá logs reales,
esta es la entrada de todo el flujo. El escenario `bank` imita la aplicación del equipo: picos
por la mañana y por la tarde, errores 400 por fondos insuficientes y 404 de
bots. `--ground-truth` guarda los parámetros usados, para comprobar que el
modelo los recupera. `--scenario endpoints --endpoints endpoints.json` genera
tráfico para cualquier otra aplicación escaneada. La semilla por defecto es
42: con los mismos parámetros sale el mismo fichero.

## 4. Formatos de log

`--format auto` (por defecto) mira las primeras 20 líneas y elige el formato de la primera que reconoce. Los ficheros pueden estar en UTF-8, con o sin BOM, o en la codificación de Windows (Windows-1252), con finales de línea de Linux o de Windows. Formatos:

| Formato | Ejemplo de línea | El timestamp es |
|---|---|---|
| `json` (backend) | `{"ts_end":"2026-10-01T09:25:34.548091Z","method":"POST","path":"/api/retirada","status":400,"latency_ms":0.734,"client_id":"user_002","session_id":"4ce89f0ec4024fdb9d390429d50db6a6"}` | el fin (clave `ts_end`; se detecta sola) |
| `jsonl` | `{"timestamp": "2026-09-28T10:00:00Z", "session_id": "s1", "method": "GET", "path": "/api/saldo/user_001", "status": 200, "latency_ms": 20, "message": "ok"}` | el inicio |
| `pipe` | `2026-10-01T09:25:34.548091Z \| POST \| /api/retirada \| 400 \| 0.734 \| user_002 \| 4ce89f0ec4024fdb9d390429d50db6a6` | el fin (así lo escribe el backend) |
| `clf` | `10.0.0.1 - - [28/Sep/2026:12:00:00 +0200] "GET /a HTTP/1.1" 200 512 "-" "curl" 0.015` | el inicio |
| `regex` | cualquiera, con una expresión regular | configurable |

El formato `pipe` es el `trafico.log` anterior del backend (el actual es JSON, ver `docs/contracts.md`): `TIMESTAMP | METODO | RUTA | STATUS | MS | ID_CLIENTE | ID_SESION`. La 7.ª columna es el id de la cookie `MORFEO_SID` del servidor, y con ella las sesiones salen del log (`"session_source": "session_id"` en `analysis.json`). También se aceptan las líneas de 6 columnas de los logs antiguos, sin sesión: entonces se agrupa por cliente con el corte de 30 minutos. Las dos variantes se leen sin `--config`, incluso mezcladas en la misma carpeta.

`jsonl` acepta también `client_id`. `clf` es el formato de Apache y Nginx; el
último campo, opcional, es el tiempo de respuesta en segundos.

Para otro formato se pasa un JSON con `--config parser.json`. Por ejemplo, un
log JSON con otros nombres de campo, campos anidados y timestamp en epoch:

```json
{
  "format": "jsonl",
  "fields": {"timestamp": "ts", "method": "http.method", "path": "http.path",
             "status": "http.status", "latency_ms": "dur_s", "client_id": "user"},
  "timestamp_format": "epoch_ms",
  "latency_unit": "s",
  "timestamp_is": "end"
}
```

O un log de texto con una expresión regular (los grupos con nombre son los
campos):

```json
{
  "format": "regex",
  "pattern": "^(?P<timestamp>[^,]+),(?P<method>\\w+),(?P<path>[^,]+),(?P<status>\\d+),(?P<latency_ms>\\d+)$",
  "timestamp_format": "%Y-%m-%d %H:%M:%S",
  "input_timezone": "Europe/Madrid"
}
```

Todas las opciones:

| Opción | Uso | Por defecto |
|---|---|---|
| `fields` | nombre del campo en el log, con punto para anidados | los del formato jsonl |
| `pattern` | regex con grupos `timestamp`, `method`, `path`, `status`, `latency_ms`, `client_id`, `session_id`, `message` | |
| `pipe_columns`, `delimiter` | orden de columnas del formato pipe (`_` ignora una) y separador. Si se fija, se exige ese número de columnas | las 7 de `trafico.log` (la última, opcional), `\|` |
| `timestamp_is` | `start` o `end` de la petición | según el formato |
| `timestamp_format` | `null` (ISO 8601), `epoch_s`, `epoch_ms` o formato `strftime` | `null` |
| `input_timezone` | zona de los timestamps sin zona | `UTC` |
| `latency_unit` | `ms`, `s` o `us` | `ms` |
| `missing_client_values` | valores que significan sin cliente | `UNKNOWN`, `-`, vacío |
| `skip_invalid` | descartar líneas malformadas (con aviso) o fallar en la primera | `true` |

`morfeo analyze --strict` equivale a `skip_invalid: false`.

## 5. Ficheros que se generan

El formato exacto de cada fichero está en [docs/contracts.md](docs/contracts.md).
Todos son deterministas: con las mismas entradas y la misma semilla salen
idénticos byte a byte.

| Fichero | Lo escribe | Contenido |
|---|---|---|
| `endpoints.json` | scan | un objeto por endpoint: método, ruta, expresión regular de la ruta, parámetros, cuerpo con esquema y ejemplo, clase Java de origen |
| `requests.csv` | analyze | una fila por petición con sesión, posición, inicio, fin, endpoint normalizado, estado, latencia y espera |
| `analysis.json` | analyze | estadísticas descriptivas de los logs |
| `profile.json` | train | el modelo: matriz de transición, esperas, longitud de sesión, curva horaria y usuarios concurrentes |
| `validation.json` | train y validate | la comparación entre sesiones sintéticas y reales |

Un endpoint en `endpoints.json`:

```json
{
  "id": "POST /api/ingreso",
  "method": "POST",
  "path": "/api/ingreso",
  "path_regex": "^/api/ingreso$",
  "framework": "spring",
  "consumes": ["application/json"],
  "params": [],
  "body": {
    "java_type": "com.example.bankapi.dto.MovimientoRequest",
    "content_type": "application/json",
    "schema": {"type": "object", "properties": {"id": {"type": "string"}, "cantidad": {"type": "number", "format": "decimal"}}},
    "example": {"id": "string", "cantidad": 1.0}
  }
}
```

La matriz de transición de `profile.json` (redondeada). Cada fila suma 1:

```json
"START":               {"GET /api/saldo/{id}": 0.804, "POST /api/ingreso": 0.069, "POST /api/retirada": 0.043, "POST /api/usuario/crear": 0.084},
"GET /api/saldo/{id}": {"GET /api/saldo/{id}": 0.095, "POST /api/ingreso": 0.307, "POST /api/retirada": 0.306, "END": 0.292},
"POST /api/retirada":  {"GET /api/saldo/{id}": 0.644, "POST /api/ingreso": 0.061, "POST /api/retirada": 0.049, "END": 0.246}
```

Se lee así: el 80 % de las sesiones empieza consultando el saldo, y después
de una retirada el 64 % de los usuarios vuelve a consultar el saldo.

Una hora de la curva:

```json
{"hour": 10, "sessions_per_hour": 40.4, "requests_per_hour": 144.4, "rps": 0.040111,
 "mean_session_duration_s": 30.2,
 "concurrent_users": {"little": 0.339, "observed_mean": 0.339, "observed_p95": 1.0, "observed_max": 5, "recommended": 1}}
```

- `little`: usuarios a la vez según la ley de Little (sesiones que llegan por
  segundo por duración media de sesión).
- `observed_*`: sesiones activas contadas segundo a segundo en esa hora.
  Si `little` y `observed_mean` salen parecidos, las dos medidas se confirman.
- `recommended`: el percentil 95 observado, redondeado hacia arriba. Es el
  valor por defecto de usuarios para esa franja en el plan de carga.

## 6. Cómo leer el informe de validación

La validación genera con el modelo 10.000 sesiones sintéticas (o tantas como
reales, si hay más) y las compara con las reales. Usar muchas sesiones
sintéticas hace que el resultado no dependa de la suerte del sorteo, también
cuando hay pocas sesiones reales.

| Línea | Qué mide | Bien si |
|---|---|---|
| Tiempo de espera, KS | test de Kolmogorov-Smirnov entre esperas reales y sintéticas: D es la mayor distancia entre las dos distribuciones acumuladas | D <= 0,1 |
| Peticiones por sesión, KS | lo mismo con el número de peticiones por sesión | D <= 0,1 |
| Matriz de transición, Markov | distancia entre la matriz real y la de las sesiones sintéticas. TV ponderada es la diferencia media por fila, ponderada por lo visitada que está cada fila | TV <= 0,05 |
| Matriz de transición, base | lo mismo con el modelo sin memoria. Sirve de referencia | más alta que la de Markov |
| Mezcla de endpoints | diferencia en el peso de cada endpoint | cerca de 0 |

El veredicto usa D y no el p-valor. Con muchas muestras, el p-valor sale bajo
incluso con diferencias pequeñas que no importan para una prueba de carga. El
p-valor aparece como información.

Si la TV ponderada de Markov es parecida a la del modelo base, el orden de las
peticiones no aporta información y los endpoints se pueden elegir según su peso.

## 7. Decisiones de diseño

| Decisión | Alternativas | Motivo |
|---|---|---|
| Leer el bytecode con un parser propio del formato `.class` | `javap -v -p` por subprocess; librería `jawa` | No necesita JDK para ejecutarse y no depende del texto de `javap`, que está pensado para personas. Lee las 4.652 clases del jar del backend en 3 s. Un test compara sus anotaciones con las de `javap` en todas las clases de ejemplo |
| Detector de Javalin además de Spring | solo Spring | La primera versión del backend era Javalin y otras aplicaciones lo usan. El backend actual es Spring Boot 3 y lo lee el detector de Spring |
| Heredar mappings de interfaces y superclases | solo anotaciones de la propia clase | OpenAPI generator pone las anotaciones en interfaces (por ejemplo en spring-petclinic-rest). Spring las hereda igual |
| Campos de formulario obligatorios solo con `@Valid` | usar siempre `@NotBlank` y similares | Sin `@Valid`, Spring no valida y el campo puede faltar |
| Detección automática del formato de log y configuración en JSON | un único formato fijo | El backend escribe un formato propio y otras aplicaciones usarán JSON lines o el formato de Nginx |
| Sesiones por cliente con corte de 30 minutos cuando no hay `session_id` | una sesión por cliente; ventanas fijas | Es el criterio habitual en analítica web. El umbral es configurable |
| Espera = inicio de la siguiente menos fin de la anterior | diferencia entre timestamps | Separa el tiempo de respuesta del servidor del tiempo del usuario, que es lo que se reproduce con los timers |
| Normalizar primero contra `endpoints.json` y después por regla | solo reglas | Las plantillas del jar son exactas y se unen con los endpoints sin ambigüedad |
| Excluir rutas sin endpoint al entrenar | conservarlas | Son 404 de bots y rutas inexistentes. Con ellas, 4 endpoints se convertían en 24 estados |
| Concurrencia contada segundo a segundo, más la ley de Little | contar sesiones que tocan cada minuto | Contar por minuto inflaba la concurrencia (2,96 frente a 0,83). En el instante, las dos medidas coinciden y se comprueban entre sí |
| Usuarios recomendados = percentil 95 | media; máximo | La media se queda corta en los minutos de más tráfico y el máximo depende de un solo pico |
| Test KS implementado en numpy | scipy | Las librerías permitidas eran pandas, numpy, lxml, scikit-learn y pytest. El estadístico coincide con scipy (test incluido) |
| Validar con las sesiones más recientes (`--holdout`) | muestra aleatoria | Imita el uso real: el modelo se entrena con el pasado y se aplica al futuro |
| Sin scikit-learn | usarlo para la cadena de Markov | La matriz de transición es un conteo. No aporta nada |
| Semilla 42 y salidas byte a byte iguales | | Resultados reproducibles, y diferencias visibles en git |

## 8. Limitaciones conocidas

- **Javalin:** las rutas construidas en tiempo de ejecución (concatenando
  variables) no se pueden leer del bytecode. Se omiten y salen como aviso.
- **Spring:**
  - De los `@InitBinder` se leen `setAllowedFields` y `setDisallowedFields`
    con textos constantes (por ejemplo `id` en PetClinic). Una lista
    construida en tiempo de ejecución no se lee.
  - Las anotaciones de mapping propias (una `@GetJson` que envuelva
    `@GetMapping`) no se reconocen. Las anotaciones de controlador propias sí.
- **Nombres de parámetros:** si el jar se compiló sin `-parameters` ni `-g` y
  la anotación no da el nombre, el parámetro sale como `arg0` con un aviso.
  Spring Boot activa `-parameters` por defecto.
- **Logs:**
  - Si una carpeta mezcla formatos, se usa el del primer fichero y las líneas
    del resto se descartan con aviso.
  - Las peticiones sin cliente ni `session_id` se agrupan por tiempo. Con
    usuarios anónimos concurrentes, sus peticiones se mezclan.
- **Datos:** los logs no guardan cuerpos ni valores de parámetros. El ejemplo
  de `endpoints.json` sirve de plantilla, pero los datos reales (IDs de
  cliente, importes) los tiene que poner el plan de carga.
- **Modelo:** la cadena de Markov es de primer orden: el siguiente endpoint
  depende solo del actual, no de los anteriores.

## 9. Tests

```bash
python -m pytest
```

Son 141 tests (140 pasan y 1 se salta) y tardan unos 3 minutos, porque
algunos ejecutan JMeter de verdad; si `JMETER_HOME` no está definido, esos se
saltan. Los del escáner compilan con `javac` una app
Spring de ejemplo, una app Javalin y un war, y montan los jars igual que
Spring Boot. Si no hay JDK instalado, esos tests se saltan. El test de
integración ejecuta `morfeo generate` sobre el jar real del backend.

## 10. Estructura del código

```
morfeo-python/
  pyproject.toml
  docs/
    contracts.md          formato exacto de los ficheros intermedios
    jmeter-handoff.md     qué campo sirve para cada parte del plan JMeter
  src/morfeo/
    cli.py                comando morfeo
    common.py             semilla, JSON determinista, lectura y escritura de requests.csv
    jar_scanner/
      classfile.py        parser del formato .class
      bytecode.py         instrucciones del bytecode
      descriptors.py      tipos y genéricos
      jarfile.py          layouts spring-boot, war y plain
      appconfig.py        application.properties y application.yml
      spring.py           reglas de Spring Web MVC
      javalin.py          detector de rutas Javalin
      dto.py              esquema y ejemplo de los DTO
      scanner.py          escaneo completo
    log_analyzer/
      config.py           formatos y configuración del parser
      parsers.py          lectura de logs
      normalize.py        rutas a plantillas
      sessions.py         sesiones y tiempos de espera
      stats.py            estadísticas de analysis.json
      synthetic.py        generador de logs de prueba
      analyzer.py         análisis completo
    traffic_model/
      distributions.py    distribuciones empíricas y muestreo
      markov.py           cadena de Markov
      hourly.py           curva horaria y concurrencia
      ks.py               test de Kolmogorov-Smirnov
      model.py            entrenamiento y modelo base
      validate.py         validación e informe
    jmeter_plan/
      plan.py             perfil y endpoints a plan .jmx (Groovy, carga, usuarios)
      cli.py              morfeo jmeter
  tests/                  un directorio por módulo y fixtures (Java y logs)
```
