# DESACTUALIZADO!!!!! MORFEO: tráfico sintético a partir del uso real

Proyecto PAE de la UPC ETSETB, propuesto por ITnow. MORFEO genera pruebas de
carga (JMeter) a partir de una aplicación Java y de los logs de cómo la usan
sus usuarios.

```
 simulador-usuarios ──HTTP──> backend-java (bank-api-stress) ──> trafico.json
                                      │                              │
                                      └──── bank-api-stress.jar ─────┤
                                                                     ▼
                                       morfeo-python (morfeo): endpoints.json + profile.json
                                                                     │
                                                                     ▼
                                                          plan.jmx (morfeo jmeter)
```

| Carpeta | Qué es | Lenguaje |
|---------|--------|----------|
| [`backend-java`](backend-java/README.md) | La aplicación de referencia: una API bancaria con PostgreSQL que escribe `trafico.json` | Java 21, Spring Boot 3, PostgreSQL 16 |
| [`simulador-usuarios`](simulador-usuarios/README.md) | Genera tráfico HTTP real contra la API, imitando a usuarios | Java 21 |
| [`morfeo-python`](morfeo-python/README.md) | Bloque de procesamiento: escanea el jar, analiza los logs, entrena el modelo de tráfico y genera el plan de JMeter (`.jmx`) | Python |
| [`morfeo-lab`](morfeo-lab/README.md) | El lab: despliega **una instancia** de la API en Kubernetes (kind sobre Docker), ejecuta los `.jmx` con JMeter y mide con Prometheus y Grafana | Python |
| `orquestador-python`, `infraestructura` | Orquestación y despliegue (Docker) | (pendiente) |

> **Demo (10 minutos) en el lab con Docker, Kubernetes y Grafana:** seguid
> [morfeo-lab/TUTORIAL.md](morfeo-lab/TUTORIAL.md). Solo hay una instancia
> del servidor (bank-api), que recibe todas las peticiones de JMeter.

Este README describe la **parte Java**. El detalle de cada módulo está en su
propio README.

## Puesta en marcha

Requisitos: JDK 21+, Maven 3.8+ y Docker (PostgreSQL y tests del backend).

```bash
# 1. PostgreSQL + API (puerto 7070, log en backend-java/logs/trafico.json)
cd backend-java
mvn clean package
docker compose up -d --build
docker compose exec -T postgres psql -U banco -d banco < db/reset.sql   # datos iniciales

# 2. Tráfico de usuarios (en otra terminal), un día entero
cd simulador-usuarios
mvn clean package
java -jar target/simulador-usuarios.jar --duracion 24h --zona Europe/Madrid

# 3. Del log al modelo (bloque Python)
cd morfeo-python
morfeo generate --jar ../backend-java/target/bank-api-stress.jar \
    --logs ../backend-java/logs/trafico.json \
    --out-dir build --tz Europe/Madrid
```

Para una prueba rápida de unos minutos, sin curva horaria:

```bash
java -jar target/simulador-usuarios.jar --duracion 3m --sesiones-hora 3000 --curva plana --velocidad 10
```

## Tutorial: comprobar por vuestra cuenta que MORFEO funciona

Guía paso a paso, desde un Windows 10/11 sin nada instalado, para repetir la
comprobación de extremo a extremo de la rama `CodigoLimpio`. Al terminar habréis
visto con vuestros ojos que:

1. los tests de los tres módulos pasan;
2. la API bancaria arranca y escribe `trafico.json`;
3. el simulador genera tráfico y MORFEO aprende un modelo de él;
4. MORFEO escribe un plan de JMeter (`plan.jmx`);
5. JMeter reproduce ese plan contra la API sin errores y las sesiones salen separadas.

Tiempo estimado: 1 hora, casi todo descargas. Todos los comandos son de
**PowerShell** (no CMD). Donde un paso depende del anterior, se dice.

> Las rutas de ejemplo usan `$HOME` (`C:\Users\<vuestro usuario>`). Las
> herramientas descargadas a mano van a `$HOME\tools` y los resultados de la
> prueba a `$HOME\morfeo-prueba`, fuera del repositorio, para no ensuciarlo.

### Paso 1. Instalar las herramientas

Abrid PowerShell **como administrador** (clic derecho → Ejecutar como
administrador). `winget` ya viene con Windows 10/11; si dice que no existe,
instalad "App Installer" desde Microsoft Store.

| Herramienta | Para qué | Versión probada |
|---|---|---|
| Git | clonar el repositorio | 2.5x |
| JDK 21 | compilar y ejecutar el backend y el simulador (Java 21 obligatorio) | Oracle 21.0.2 |
| Maven | compilar los módulos Java (3.8 o superior) | 3.10.0 |
| Python 3.11 o superior | el bloque MORFEO (`morfeo-python`) | 3.14 (el proyecto pide ≥ 3.11) |
| Docker Desktop | PostgreSQL y los tests del backend (Testcontainers) | 4.95 |
| JMeter | ejecutar el plan `.jmx` | 5.6.3 |

**1.1. Git, JDK 21 y Python** (con `winget`):

```powershell
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
winget install -e --id EclipseAdoptium.Temurin.21.JDK --accept-source-agreements --accept-package-agreements
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
```

**1.2. Docker Desktop.** Necesita WSL2 y casi seguro **reiniciar Windows**:

```powershell
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
wsl --install --no-distribution --web-download
```

Reiniciad. Después abrid "Docker Desktop" desde el menú Inicio, aceptad los
términos y esperad a que abajo a la izquierda ponga "Engine running". Sin
eso, los tests del backend fallan con "Could not find a valid Docker
environment".

**1.3. Maven y JMeter** (no están en `winget`: se descargan y se comprueba su
hash SHA-512 para saber que el fichero no está corrupto):

```powershell
New-Item -ItemType Directory -Force "$HOME\tools" | Out-Null
Set-Location "$HOME\tools"

# Maven 3.9.9
Invoke-WebRequest https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip -OutFile maven.zip
Invoke-WebRequest https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip.sha512 -OutFile maven.sha512
if ((Get-FileHash maven.zip -Algorithm SHA512).Hash.ToLower() -eq ((Get-Content maven.sha512 -Raw) -split '\s+')[0].ToLower()) { Expand-Archive maven.zip . -Force; "Maven OK" } else { "HASH DISTINTO: volved a descargar" }

# JMeter 5.6.3 (unos 90 MB; puede tardar varios minutos)
Invoke-WebRequest https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.zip -OutFile jmeter.zip
Invoke-WebRequest https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.zip.sha512 -OutFile jmeter.sha512
if ((Get-FileHash jmeter.zip -Algorithm SHA512).Hash.ToLower() -eq ((Get-Content jmeter.sha512 -Raw) -split '\s+')[0].ToLower()) { Expand-Archive jmeter.zip . -Force; "JMeter OK" } else { "HASH DISTINTO: volved a descargar" }
```

Si `Expand-Archive` tarda, esperad a que vuelva el prompt: JMeter son unos 150
ficheros `.jar` y no se puede usar a medias.

### Paso 2. Preparar la consola

Cerrad PowerShell y abrid una **normal** (ya no hace falta administrador). Estas
variables valen solo para esa ventana: hay que repetirlas si abrís otra.

```powershell
$env:JAVA_HOME  = "C:\Program Files\Eclipse Adoptium\jdk-21.0.12.7-hotspot"   # ajustad a la carpeta que se haya creado
$env:JMETER_HOME = "$HOME\tools\apache-jmeter-5.6.3"
$env:Path = "$env:JAVA_HOME\bin;$HOME\tools\apache-maven-3.9.9\bin;C:\Program Files\Git\usr\bin;C:\Program Files\Docker\Docker\resources\bin;$env:Path"
```

La carpeta del JDK cambia con la versión: mirad en `C:\Program Files\Eclipse
Adoptium\`. `C:\Program Files\Git\usr\bin` aporta `awk`, que usa un test.

Comprobad que todo responde (cada línea debe imprimir una versión, no un error):

```powershell
java -version        # debe decir 21.x
mvn -v               # Apache Maven 3.9.9, y "Java version: 21..."
python --version     # 3.11 o superior
docker version       # deben salir "Client" y "Server"
git --version
```

Si `mvn -v` dice "JAVA_HOME is not defined correctly", la ruta del JDK de la
primera línea no existe: corregidla. Si `docker version` no muestra "Server",
Docker Desktop no está arrancado todavía.

### Paso 3. Descargar el código y elegir la rama

```powershell
Set-Location $HOME
git clone https://github.com/dasuarezang/pae-itnow-synthetic-traffic.git
Set-Location pae-itnow-synthetic-traffic
git checkout CodigoLimpio
git log --oneline -3     # el primero debe ser el último commit de la rama
$repo = (Get-Location).Path
```

`$repo` se usa en los pasos siguientes.

### Paso 4. Tests de los módulos Java

```powershell
Set-Location "$repo\backend-java"
mvn clean package        # compila, pasa los tests y deja target\bank-api-stress.jar
```

La primera vez descarga dependencias y tarda varios minutos, y los tests
levantan un PostgreSQL con Docker. **Resultado esperado:** `Tests run: 23,
Failures: 0, Errors: 0` y `BUILD SUCCESS`.

```powershell
Set-Location "$repo\simulador-usuarios"
mvn clean package        # esperado: Tests run: 12, Failures: 0, Errors: 0
```

Si `BancoRepositoryTest` falla con "Failed to obtain JDBC Connection" la
primera vez, repetid `mvn clean package`: nos pasó una vez justo después de
arrancar Docker y no se ha reproducido (ver "Pendiente").

### Paso 5. Tests del bloque Python

```powershell
Set-Location "$repo\morfeo-python"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest -q -rs
```

Si PowerShell dice que la ejecución de scripts está deshabilitada, ejecutad una
vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` y repetid.

**Resultado esperado:** unos 140 `passed` y 1 `skipped`, el del fichero
`morfeo-parser.json` que el equipo Java borró (test obsoleto, ver "Pendiente").
Los tests que usan el jar del backend y JMeter solo se ejecutan si el Paso 4
terminó bien y `JMETER_HOME` está definido (Paso 2); si no, salen como
`skipped` con el motivo. Tarda unos 3-4 minutos porque algunos tests ejecutan
JMeter de verdad.

### Paso 6. Arrancar la API y su base de datos

```powershell
Set-Location "$repo\backend-java"
docker compose up -d --build postgres app
```

Importante: se nombran solo `postgres` y `app`. El `docker-compose.yml`
completo incluye MinIO, cuyas imágenes ya no se descargan de Docker Hub (ver
"Pendiente"), y `docker compose up` sin nombres falla.

Esperad medio minuto y comprobad:

```powershell
(Invoke-WebRequest http://localhost:7070/actuator/health -UseBasicParsing).Content
```

Debe contener `"status":"UP"` (en Windows PowerShell 5.1 puede salir como
números ASCII; es el mismo texto). Si no responde, `docker compose logs app`
dice por qué.

Para repetir una prueba con la base de datos limpia (deja solo las 3 cuentas
iniciales):

```powershell
Get-Content db\reset.sql | docker compose exec -T postgres psql -U banco -d banco
```

(La versión con `<` del resto de la documentación solo funciona en Bash.)

### Paso 7. Generar tráfico con el simulador

En la misma consola, con la API arrancada:

```powershell
Set-Location "$repo\simulador-usuarios"
java -jar target\simulador-usuarios.jar --duracion 4m --sesiones-hora 3000 --curva plana --velocidad 10 --zona Europe/Madrid
```

Tarda unos 6 minutos (4 de sesiones nuevas y hasta 2 de espera a las que siguen
activas) y cada minuto imprime una línea de progreso. **Resultado esperado:**
unas 200 sesiones y unas 700 peticiones, casi todas `200` y `201`, y algunas
`400` y `404` (JSON roto y bots a propósito). **Ningún `5xx`.**

Esta prueba es rápida y no vale para entrenar de verdad: `--velocidad 10`
divide las esperas entre 10, y el modelo real necesita 10.000 sesiones o más
(ver "Pendiente").

Comprobad que el backend escribió el log:

```powershell
$log = "$repo\backend-java\logs\trafico.json"
(Get-Content $log | Measure-Object -Line).Lines     # un número parecido a las peticiones
Get-Content $log -TotalCount 2                      # JSON, uno por línea, con ts_end, method, path...
```

Apuntad ese número de líneas: es `$antes` para el Paso 9.

```powershell
$antes = (Get-Content $log | Measure-Object -Line).Lines
```

### Paso 8. Del log al modelo y al plan de JMeter

```powershell
Set-Location "$repo\morfeo-python"
.\.venv\Scripts\Activate.ps1
$out = "$HOME\morfeo-prueba"
morfeo generate --jar ..\backend-java\target\bank-api-stress.jar --logs $log --out-dir $out --tz Europe/Madrid
```

**Resultado esperado**, por orden:

- `scan: 4 endpoints` (los 4 de la API, leídos del jar sin ejecutarlo).
- `analyze: <n> peticiones, <m> sesiones, 6 sin endpoint` (los bots que piden
  rutas que no existen).
- `train: 4 endpoints`.
- La **validación del modelo**, que debe acabar en `Veredicto: tiempos de espera
  correcto, longitud de sesión correcto, transiciones correcto`. Si dijera
  "incorrecto", el modelo no representa bien el tráfico.
- La tabla del plan (un Thread Group por hora con sesiones) y `Plan escrito en
  ...\plan.jmx`.

En `$HOME\morfeo-prueba` quedan `endpoints.json`, `analysis.json`,
`requests.csv`, `profile.json`, `validation.json` y `plan.jmx`.

### Paso 9. Ejecutar el plan con JMeter

```powershell
Set-Location $out
& "$env:JMETER_HOME\bin\jmeter.bat" -n -t plan.jmx -l results.jtl -j jmeter.log `
    -Jhost=localhost -Jport=7070 `
    -Lorg.apache.jmeter.protocol.http.sampler.HTTPSamplerBase=ERROR
```

(En PowerShell la línea se continúa con el acento grave `` ` `` al final, no con `\`.)

Tarda de 1 a 2 minutos. Las líneas `WARN StatusConsoleListener` son normales.
**Resultado esperado** al final:

- `summary = <n> in ... Err: 0 (0.00%)`: cero errores.
- una línea `MORFEO: hora ...` con las sesiones empezadas y `0 sin empezar por
  falta de hilos libres`.

### Paso 10. Comprobar lo que JMeter ha hecho en la API

```powershell
Get-Content $log | Select-Object -Skip $antes | ConvertFrom-Json | Group-Object status | Select-Object Name, Count | Format-Table -AutoSize
Get-Content $log | Select-Object -Skip $antes | ConvertFrom-Json | Select-Object -ExpandProperty session_id -Unique | Measure-Object | Select-Object -ExpandProperty Count
```

**Resultado esperado:**

- El número de líneas nuevas es igual al `<n>` de JMeter del Paso 9.
- No aparece ningún `5xx`. Verás muchos `409`: son las altas del pool de
  usuarios (el plan da de alta a los usuarios que ya existían por el simulador,
  y la API responde "ya existe"). Es lo previsto. Los `4xx` también son uso
  normal (por ejemplo, una retirada sin fondos).
- Varias sesiones distintas. Si hubiera una sesión por petición, el plan no
  tendría el *HTTP Cookie Manager* y estaría mal.

**Si vais a volver a pasar este log por `morfeo generate`**, quitad antes las
altas del pool, o contarán como sesiones de usuarios. El comando exacto lo
imprime `morfeo generate` al final (empieza por `awk`); se ejecuta en Git Bash
sobre `trafico.json`.

### Paso 11. Apagar y limpiar

```powershell
Set-Location "$repo\backend-java"
docker compose down        # apaga la API y PostgreSQL; los datos se conservan
docker compose down -v     # además borra los datos de la base de datos
```

### Si algo falla

| Síntoma | Causa y solución |
|---|---|
| `Could not find a valid Docker environment` en `mvn` | Docker Desktop no está arrancado. Ábrelo y espera a "Engine running" |
| `docker` no se reconoce | Falta `C:\Program Files\Docker\Docker\resources\bin` en el `Path` (Paso 2), o hay que reiniciar |
| `JAVA_HOME is not defined correctly` | La ruta de `$env:JAVA_HOME` no existe; mira la carpeta real en `C:\Program Files\Eclipse Adoptium\` |
| `invalid target release: 21` | `java -version` no es 21: otro JDK va primero en el `Path` |
| `pull access denied for minio/minio` | `docker compose up` sin nombres. Usad `docker compose up -d --build postgres app` |
| `WinError 193` en los tests de JMeter | `JMETER_HOME` apunta a una carpeta sin `bin\jmeter.bat`, o se usó una versión antigua de `test_plan.py` |
| Todos los tests de JMeter `skipped` | `JMETER_HOME` no está definido en esa consola |
| `Address already in use` / el puerto 7070 no responde | Hay otra app en el 7070, o la API aún no ha arrancado (`docker compose logs app`) |
| Muchas sesiones de 1 petición en el log | El cliente no guarda la cookie `MORFEO_SID` |

## Qué se ha hecho en la parte Java

Parte del backend inicial (Javalin, Java 17) y de la revisión de entradas que
se pasó al bloque Python.

### 1. Revisión de la lista de problemas

| # | Problema | Solución |
|---|----------|----------|
| 1 | El enunciado pide Spring Boot y la app era Javalin | Migrada a **Spring Boot 3.5** (Spring MVC + Tomcat). Mismos 4 endpoints, mismos DTO, misma lógica |
| 2 | Compilaba para Java 17 y se pide Java 21 | Compila para **Java 21** |
| 3 | `.gitignore.txt` mal nombrado, con `target/` y los jar en git | Renombrado a `.gitignore`; `target/`, `trafico.json` y `dependency-reduced-pom.xml` fuera de git |
| 4 | El log no tenía `session_id` | Cookie de sesión `MORFEO_SID` y **7.ª columna** `ID_SESION` en el log |
| 5 | Solo había 3 líneas de log real | Nuevo módulo **`simulador-usuarios`** que genera tráfico real contra la API |
| 6 | Puerto y ruta del log fijos | Variables de entorno `PORT`, `TRAFFIC_LOG`, `MAX_THREADS`, `SESSION_TIMEOUT_MIN` |
| 7 | El README decía `cd bank-api-stress` | Corregido |

### 2. Log de tráfico fiable

`trafico.json` es la entrada de todo MORFEO, así que se ha revisado para que
ninguna línea se pierda ni se lea mal:

- **Inyección en el log:** un `id` con `|` o un salto de línea podía partir una
  línea o fabricar líneas falsas. Ahora se sustituyen por `_`.
- **Timestamp** de ancho fijo (UTC, microsegundos). Antes cambiaba de longitud
  según los ceros finales.
- **`TIEMPO_MS` con tres decimales.** En milisegundos enteros casi todas las
  peticiones salían como `0` o `1`. Siempre con punto decimal, aunque el
  sistema esté en español.
- **UTF-8** explícito (antes, la codificación del sistema: Windows-1252).
- **Logger bajo carga:** cola acotada (100.000 líneas) en lugar de crecer hasta
  agotar la memoria, escritura por lotes y reintento si el disco falla.
- **Códigos de error correctos:** antes cualquier excepción se respondía con
  400, así que un fallo del servidor contaba como error del cliente. Ahora es
  400 solo para el JSON inválido, 404/405/415 para lo que corresponde y 500
  para el resto, sin enseñar el mensaje interno.

### 3. Sesiones

El log trae ahora un identificador de sesión, y MORFEO reconstruye las sesiones
exactas en lugar de adivinarlas por inactividad.

- La API da una cookie `MORFEO_SID` a quien no la trae y la renueva en cada
  respuesta con `Max-Age` de 30 minutos (configurable). Tras 30 minutos sin
  peticiones, la sesión caduca.
- El servidor no guarda estado (no usa `HttpSession`): una prueba con millones
  de sesiones no consume memoria.
- Los clientes deben guardar la cookie: en JMeter, con un *HTTP Cookie Manager*.

### 4. Simulador de usuarios

Llegan sesiones según la hora del día (picos a las 10 h y a las 18-19 h). Cada
cliente se da de alta en su primera visita y recorre los endpoints con esperas
realistas. También hay retiradas sin fondos, algún JSON roto y bots que piden
rutas inexistentes. Detalle en [su README](simulador-usuarios/README.md).

### 5. Base de datos

El laboratorio irá en Kubernetes con varias réplicas de la app. Con los datos en
memoria, cada pod tenía sus propios saldos y usuarios. Ahora están en
**PostgreSQL 16**, compartido por todas las réplicas:

- **Atomicidad en la base de datos:** cada operación es una sola sentencia SQL
  (`UPDATE ... WHERE saldo >= ? RETURNING saldo`), con `JdbcTemplate` y sin JPA.
  Sigue siendo atómica aunque haya varias réplicas, cosa que el `synchronized` en
  memoria no garantizaba.
- **Dos colas que estudiar:** la web (hilos de Tomcat: `MAX_THREADS`,
  `ACCEPT_COUNT`) y la del DataSource (pool de HikariCP: `DB_POOL_SIZE`,
  `DB_TIMEOUT_MS`). Se observan en `/actuator/metrics`.
- **503** cuando la base de datos no responde o no hay conexión libre a tiempo:
  es el síntoma de la cola del DataSource saturada, distinto de un 500.
- **Datos repetibles:** sobreviven a los reinicios, y antes de cada experimento
  se vuelven al estado inicial con `db/reset.sql`. La app no los borra al
  arrancar, porque con varias réplicas un reinicio borraría los de todos.
- **Probes de Kubernetes:** liveness sin la base de datos y readiness con ella.
- `Dockerfile` y `docker-compose.yml` (PostgreSQL + app) para el laboratorio local.

El contrato con el bloque Python no cambia, salvo el 503 (J-003 en `coordinacion/`).

### 6. Otros

- `/actuator/health` para el healthcheck de Docker. No se escribe en el log y el
  escáner de MORFEO no lo cuenta como endpoint.
- Apagado ordenado: con Ctrl+C o `docker stop` se terminan las peticiones en
  curso y se vacía el log.

### Tests

| Módulo | Qué cubren |
|--------|------------|
| `backend-java` | Atomicidad contra un PostgreSQL real (Testcontainers, necesita Docker); formato y saneado del log; la app real por HTTP: endpoints, errores, sesiones, healthcheck, métricas, 1.000 retiradas concurrentes; 503 y probes sin base de datos |
| `simulador-usuarios` | Opciones, curva horaria, popularidad; sesiones contra un servidor falso (cookies, alta de clientes, cuerpos válidos, bots) |

```bash
mvn test    # en backend-java o en simulador-usuarios
``