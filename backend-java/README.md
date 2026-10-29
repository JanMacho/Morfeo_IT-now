# bank-api-stress

API REST bancaria en **Spring Boot 3 (Java 21)** con **PostgreSQL**, pensada como
diana de pruebas de estrés con JMeter y como aplicación de referencia de MORFEO:
cada petición deja una línea en `trafico.json`, que es la entrada del
`log_analyzer`.

Los datos están en PostgreSQL, no en memoria, para que varias réplicas de la app
(Docker, Kubernetes) compartan los mismos saldos y usuarios.

## Requisitos
- JDK 21+ y Maven 3.8+
- PostgreSQL 16. Lo más cómodo es Docker, con `docker compose`.
- Docker también para los tests (Testcontainers).

## Arrancar con Docker Compose

```bash
cd backend-java
mvn clean package -DskipTests      # el Dockerfile copia el jar ya compilado
docker compose up -d --build
```

Levanta PostgreSQL (`banco`/`banco`/`banco`, datos en el volumen `pgdata`) y la
app en `http://localhost:7070`. La app espera a que PostgreSQL esté sano. El log
de tráfico queda en `backend-java/logs/trafico.json`.

```bash
docker compose logs -f app    # consola de la app
docker compose down           # parar (los datos se conservan)
docker compose down -v        # parar y borrar los datos
```

## Arrancar sin Docker

Con un PostgreSQL propio (por defecto, `localhost:5432`, base de datos, usuario y
contraseña `banco`):

```bash
java -jar target/bank-api-stress.jar
DB_URL=jdbc:postgresql://otro-host:5432/banco DB_USER=... DB_PASSWORD=... java -jar target/bank-api-stress.jar
```

Si no hay base de datos al arrancar, la app no arranca: falla al crear la tabla.

## Datos

Al arrancar, cada réplica ejecuta `schema.sql` (crea la tabla `cuentas` si no
existe) y `data.sql` (añade los clientes precargados si no existen):

| id       | saldo inicial |
|----------|---------------|
| user_001 | 1000.00       |
| user_002 | 500.00        |
| user_003 | 2500.00       |

Las dos se pueden ejecutar muchas veces, y con varias réplicas arrancando a la
vez. **No borran nada**: los datos sobreviven a los reinicios de la app.

### Resetear antes de cada experimento

```bash
docker compose exec -T postgres psql -U banco -d banco < db/reset.sql
```

`db/reset.sql` borra todas las cuentas y vuelve a cargar las 3 iniciales. La app
no lo hace al arrancar: con varias réplicas, un pod que se reiniciara a mitad de
una prueba borraría los datos de todos.

### Varias réplicas

Todas las réplicas apuntan a la misma base de datos. Cada operación es una sola
sentencia SQL atómica (por ejemplo, `UPDATE ... SET saldo = saldo - ? WHERE id = ?
AND saldo >= ? RETURNING saldo`), así que un usuario creado en una réplica se ve
en las demás, y el saldo nunca queda negativo aunque lleguen retiradas
simultáneas por réplicas distintas.

```bash
PORT=7071 java -jar target/bank-api-stress.jar &
PORT=7072 java -jar target/bank-api-stress.jar &
```

## Configuración

Variables de entorno, todas opcionales (definidas en `application.properties`):

| Variable              | Por defecto   | Uso |
|-----------------------|---------------|-----|
| `PORT`                | `7070`        | puerto HTTP |
| `TRAFFIC_LOG`         | `trafico.json` | ruta del log de tráfico (relativa al directorio de trabajo) |
| `SESSION_TIMEOUT_MIN` | `30`          | minutos de inactividad tras los que caduca la sesión |
| `MAX_THREADS`         | `200`         | **cola web:** hilos de Tomcat, peticiones atendidas a la vez |
| `ACCEPT_COUNT`        | `100`         | **cola web:** conexiones que esperan cuando todos los hilos están ocupados |
| `DB_URL`              | `jdbc:postgresql://localhost:5432/banco` | base de datos |
| `DB_USER` / `DB_PASSWORD` | `banco` / `banco` | credenciales |
| `DB_POOL_SIZE`        | `10`          | **cola del DataSource:** conexiones del pool (HikariCP) |
| `DB_TIMEOUT_MS`       | `5000`        | **cola del DataSource:** cuánto espera una petición a que quede una conexión libre; si no la consigue, 503 |
| `DB_INIT`             | `always`      | `never` para no ejecutar `schema.sql`/`data.sql` al arrancar |

Límite global: las conexiones de todas las réplicas comparten el máximo de
PostgreSQL (`max_connections`, 100 por defecto), así que réplicas × `DB_POOL_SIZE`
tiene que quedar por debajo.

### Las dos colas

Una petición espera primero a un hilo de Tomcat (**cola web**) y, dentro de él,
a una conexión del pool (**cola del DataSource**). Bajo carga, se llena antes la
del DataSource: los hilos de Tomcat se quedan bloqueados esperando conexión, la
latencia sube y, si la espera supera `DB_TIMEOUT_MS`, la petición responde 503.

| Qué medir | Dónde |
|-----------|-------|
| Espera en la cola web | tiempo visto por el cliente − `TIEMPO_MS` del log (el log no incluye esa espera) |
| Hilos de Tomcat ocupados | `GET /actuator/metrics/tomcat.threads.busy` |
| Peticiones esperando conexión | `GET /actuator/metrics/hikaricp.connections.pending` |
| Conexiones en uso / tiempo para obtenerla / timeouts | `hikaricp.connections.active`, `hikaricp.connections.acquire`, `hikaricp.connections.timeout` |
| Lado de PostgreSQL | `SELECT * FROM pg_stat_activity` |

### Health y probes

| Ruta | Para qué | Mira la BD |
|------|----------|------------|
| `/actuator/health` | healthcheck de Docker; lo usa `simulador-usuarios` antes de empezar | sí (`DOWN` y 503 si no hay BD) |
| `/actuator/health/liveness` | liveness de Kubernetes: si falla, se reinicia el pod | **no**: reiniciar no arregla una BD caída |
| `/actuator/health/readiness` | readiness de Kubernetes: si falla, el pod sale del balanceo | sí |

Ni `/actuator/*` ni las métricas se escriben en `trafico.json`, y el `jar_scanner`
de MORFEO no las cuenta como endpoints.

## Endpoints

| Método | Ruta | Cuerpo | Respuestas |
|--------|------|--------|------------|
| GET  | `/api/saldo/{id}`     | | 200, 404, 503 |
| POST | `/api/ingreso`        | `{"id": "...", "cantidad": X}` | 200, 400, 404, 503 |
| POST | `/api/retirada`       | `{"id": "...", "cantidad": X}` | 200, 400 (también sin fondos), 404, 503 |
| POST | `/api/usuario/crear`  | `{"id": "...", "saldo_inicial": X}` | 201, 400, 409, 503 |

| Código | Cuándo |
|--------|--------|
| 400 | cuerpo ausente, JSON mal formado o con tipos incorrectos, campos que faltan, cantidad no positiva, fondos insuficientes |
| 404 | cliente o ruta inexistente |
| 405 / 415 | método no permitido / falta `Content-Type: application/json` |
| 409 | el usuario ya existe |
| 500 | error interno (el detalle sale por la consola, no en la respuesta) |
| 503 | `{"error": "Base de datos no disponible"}`: la BD no responde o no hay conexión libre en el pool antes de `DB_TIMEOUT_MS` |

```bash
curl http://localhost:7070/api/saldo/user_001

curl -X POST http://localhost:7070/api/ingreso \
  -H "Content-Type: application/json" \
  -d '{"id":"user_001","cantidad":100.50}'

curl -X POST http://localhost:7070/api/retirada \
  -H "Content-Type: application/json" \
  -d '{"id":"user_001","cantidad":50}'

curl -X POST http://localhost:7070/api/usuario/crear \
  -H "Content-Type: application/json" \
  -d '{"id":"user_004","saldo_inicial":300}'
```

Las rutas y los DTO (`com.example.bankapi.dto.*`) son parte del contrato con el
`jar_scanner` de MORFEO (`endpoints.json`, y el test `test_backend_jar`). Si se
cambian, hay que avisar al bloque de Python.

## Log de tráfico

Cada petición HTTP, exitosa o fallida, añade una línea al fichero `TRAFFIC_LOG`:

```
TIMESTAMP_ISO8601 | METODO_HTTP | ENDPOINT | STATUS_CODE | TIEMPO_MS | ID_CLIENTE | ID_SESION
```

```
2026-10-01T09:25:34.548091Z | POST | /api/retirada | 400 | 0.734 | user_002 | 4ce89f0ec4024fdb9d390429d50db6a6
```

Reglas que no se deben romper sin avisar al `log_analyzer`:

- Siempre 7 campos separados por `|`. Si el endpoint o los ids traen `|`,
  saltos de línea u otros caracteres de control, se sustituyen por `_`.
- `TIMESTAMP_ISO8601` es el **fin** de la petición, en UTC, con microsegundos.
- `TIEMPO_MS` lleva tres decimales y punto decimal. Mide desde que la petición
  entra en la aplicación hasta que sale: no incluye la espera en la cola de Tomcat.
- `ID_CLIENTE` sale de `{id}` en la ruta o del campo `id` del cuerpo; `UNKNOWN`
  si no hay (por ejemplo, rutas inexistentes).
- Fichero en UTF-8.

MORFEO lo lee directamente (formato `pipe`, que acepta 6 y 7 columnas):

```bash
morfeo generate --jar backend-java/target/bank-api-stress.jar --logs trafico.json --out-dir build
```

Para generar tráfico real contra la API, ver [`simulador-usuarios`](../simulador-usuarios/README.md).

### Varias réplicas en Kubernetes: juntar los logs

Cada réplica escribe su propio `trafico.json` **en su disco local**: con varios
pods no hay un solo fichero, hay uno por pod. El formato de línea no cambia,
solo hace falta juntarlos antes de que `log_analyzer` (o lo que decida los
parámetros de la prueba) los lea.

Lo hacemos con un sidecar por pod (`log-shipper`, en `docker-compose.yml` y
`scripts/ship-logs.sh`) que sube periódicamente una foto de `TRAFFIC_LOG` a
MinIO/S3, a `trafico/<hostname>.log`. En Kubernetes el hostname del contenedor
es el nombre del pod por defecto, así que cada réplica sube a su propia clave
sin configurar nada aparte. Cada subida sobreescribe el mismo objeto: no
acumula histórico, solo la última foto completa de esa réplica.

Para construir el fichero agregado, `scripts/fetch-aggregated-log.sh` descarga
todos los `trafico/*.log` del bucket y los concatena (las líneas no llevan
cabecera ni dependen del orden, así que concatenar basta):

```bash
MINIO_ENDPOINT=http://localhost:9000 MINIO_BUCKET=trafico-logs \
MINIO_ACCESS_KEY=morfeo MINIO_SECRET_KEY=morfeo-secreto \
./scripts/fetch-aggregated-log.sh trafico-aggregated.log

morfeo generate --jar target/bank-api-stress.jar --logs trafico-aggregated.log --out-dir build
```

En local (`docker compose up`) solo hay una réplica, pero el mecanismo es el
mismo: `docker compose up -d` levanta MinIO (consola en
`http://localhost:9001`, `morfeo`/`morfeo-secreto`), crea el bucket
`trafico-logs` y el sidecar empieza a subir.

En Kubernetes esto se traduce en:

- El mismo sidecar (`ship-logs.sh`) como contenedor adicional en el pod de la
  app, compartiendo el volumen donde está `TRAFFIC_LOG` (p. ej. un `emptyDir`
  montado en ambos contenedores). No hace falta `ReadWriteMany` ni permisos de
  `kubectl exec` entre pods.
- MinIO como `Deployment` + `Service` dentro del propio clúster (o un S3 real
  si el entorno final lo tiene).
- El paso que decide los parámetros de la prueba corre
  `fetch-aggregated-log.sh` antes de leer los logs, igual que en local.

### Sesiones

`ID_SESION` viene de la cookie `MORFEO_SID`. Si una petición no la trae (o trae
un valor que no generó el servidor), empieza una sesión nueva. Cada respuesta
renueva la cookie con `Max-Age = SESSION_TIMEOUT_MIN`, así que la sesión caduca
tras ese tiempo sin peticiones.

El servidor no guarda estado de sesión (no usa `HttpSession`), así que una
prueba con millones de sesiones no consume memoria. A cambio, el cliente tiene
que guardar la cookie:

- **JMeter:** añade un *HTTP Cookie Manager* al Thread Group. Sin él, cada
  petición cuenta como una sesión distinta.
- **curl:** `-b cookies.txt -c cookies.txt`.

## Notas para la prueba de estrés con JMeter

- Antes de cada prueba, resetea los datos con `db/reset.sql`.
- Apunta un Thread Group concurrente contra `POST /api/retirada` sobre el mismo
  `id` (por ejemplo `user_002`, 500 €) con cantidades pequeñas: el saldo nunca
  baja de 0 ni se corrompe, porque la retirada es una sola sentencia SQL atómica
  (`BancoRepository.retirar()`), también con varias réplicas.
- El log lo escribe un logger asíncrono (cola + hilo único) que vuelca al disco
  por lotes. La cola admite 100.000 líneas pendientes. Si el disco no da abasto
  y se llena, las sobrantes se descartan en lugar de agotar la memoria, y se
  avisa por consola.
- Para parar el servidor sin perder las últimas líneas usa Ctrl+C o
  `docker stop` (SIGTERM): termina las peticiones en curso y vacía la cola.

## Tests

```bash
mvn test
```

**Necesitan Docker en marcha:** Testcontainers levanta un `postgres:16` para
todos los tests y lo elimina al acabar. Sin Docker, los tests de base de datos
fallan; para compilar igualmente, usa `mvn clean package -DskipTests`.

- `BancoRepositoryTest`: atomicidad contra PostgreSQL. 2.000 retiradas
  simultáneas sobre la misma cuenta, ingresos y retiradas mezclados que cuadran,
  altas simultáneas del mismo id y escala exacta de los importes.
- `ApiTest`: arranca la aplicación en un puerto libre y prueba endpoints,
  errores, sesiones por cookie, que el healthcheck y las métricas no se
  registran, 1.000 retiradas concurrentes por HTTP y que cada línea del log
  cumple el formato. Antes de cada test deja la tabla como `db/reset.sql`.
- `BaseDeDatosCaidaTest`: sin base de datos, la API responde 503 (no 500), lo
  registra en el log, y el pod sigue vivo (liveness) pero fuera del balanceo
  (readiness). No necesita Docker.
- `TrafficLoggerTest`: formato de línea, saneado, UTF-8, vaciado al apagar y
  descarte con la cola llena.
