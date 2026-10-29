# Tutorial: probar un plan de MORFEO en el lab (Docker + Kubernetes + Grafana)

Guía paso a paso, desde un Windows 10/11, para comprobar por vuestra cuenta que
un plan `.jmx` generado por el bloque Python (`morfeo-python`) se ejecuta en el
lab y se ve en Grafana. Es lo que se enseña en la demo.

Todos los comandos son de **PowerShell**. Tiempo: unos 30 minutos de
instalación (con dos o tres reinicios) y unos 15 minutos la primera vez que se
monta el lab (descarga unos 2 GB). Después, **la prueba de la demo dura 10
minutos** (paso 4.1).

## 0. Qué se monta (y qué no)

```
  ┌─────────────────── Docker Desktop (una sola máquina) ───────────────────┐
  │  clúster kind "morfeo" (3 nodos, cada uno es un contenedor de Docker)   │
  │                                                                         │
  │  nodo "app"                 nodo "generators"      nodo "system"        │
  │  ┌────────────────────┐     ┌───────────────┐      ┌─────────────────┐  │
  │  │ bank-api  (1 sola  │ ◄── │ JMeter        │      │ Prometheus      │  │
  │  │ instancia)         │HTTP │ (ejecuta el   │      │ Grafana :3000   │  │
  │  │ + PostgreSQL       │     │  plan.jmx)    │      │                 │  │
  │  └────────────────────┘     └───────────────┘      └─────────────────┘  │
  └─────────────────────────────────────────────────────────────────────────┘
```

**Solo hay UNA instancia del servidor** (`bank-api`): es el servidor "atacado"
y recibe todas las peticiones que hace JMeter. Es lo que se quiere medir, así
que no hace falta (ni se debe) subir réplicas:

- `targets/bank-api.yaml` tiene `replicas: 1` y `config/lab.yaml` tiene un solo
  nodo de aplicación. No los cambiéis para la demo.
- La recolección de logs de **varias réplicas con MinIO** que hay en
  `backend-java/docker-compose.yml` **no se usa** aquí.
- El resto de contenedores (los nodos de kind, PostgreSQL, Prometheus, Grafana
  y JMeter) no son más servidores: son la base de datos de esa instancia, el
  generador de carga y las herramientas de medida. JMeter va en un nodo
  distinto para no quitarle CPU al servidor.
- Usad siempre `run --generators 1`: un solo JMeter. (Por defecto el lab
  reparte planes muy grandes entre varios JMeter; la carga total sería la
  misma, pero así no hay dudas.)

## 1. Instalar lo necesario

### 1.1. Activar la virtualización en la BIOS

Sin esto Docker no arranca. Reiniciad, entrad en la BIOS/UEFI (Supr, F2 o F10
al encender; o en Windows: Configuración → Sistema → Recuperación → Inicio
avanzado → Solucionar problemas → Opciones avanzadas → Configuración de
firmware UEFI) y activad **SVM Mode** (AMD) o **Intel Virtualization
Technology / VT-x** (Intel).

Comprobación: Administrador de tareas → Rendimiento → CPU → **Virtualización:
Habilitada**.

### 1.2. WSL 2 y Docker Desktop (PowerShell **como administrador**)

```powershell
wsl --install --no-distribution
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
bcdedit /set hypervisorlaunchtype auto
```

La última línea asegura que Windows arranca el hipervisor (en algunos equipos
viene desactivado y Docker se queda en "Starting" para siempre).

**Reiniciad con Inicio → Apagar → "Actualizar y reiniciar"** si aparece, o
"Reiniciar" si no. No uséis el botón físico y, si sale "Trabajando en las
actualizaciones", esperad: ahí es cuando Windows termina de instalar la
Plataforma de Máquina Virtual. Si se corta, WSL seguirá diciendo que falta y
habrá que reiniciar otra vez.

Comprobad (PowerShell normal):

```powershell
wsl --status                       # no debe decir "unable to start"
```

**Limitad la memoria de WSL antes de abrir Docker.** Por defecto WSL (y con él
Docker) puede quedarse hasta la mitad de la RAM del PC y casi no la devuelve a
Windows: con el lab en marcha el PC llega al 100 % de memoria. Cread el
fichero `C:\Users\<vuestro usuario>\.wslconfig` con:

```ini
[wsl2]
memory=12GB

[experimental]
autoMemoryReclaim=dropcache
```

12 GB sobran para el lab, que necesita unos 7-8 GB (pide un mínimo de 6). Con
un PC de 16 GB poned `memory=8GB`. Si cambiáis el fichero con Docker abierto,
cerrad Docker Desktop y ejecutad `wsl --shutdown` para que se aplique.

Abrid **Docker Desktop**, aceptad los términos y esperad a **Engine running**.
Si Docker se abrió solo antes de que WSL estuviera listo, cerradlo (clic
derecho en la ballena → Quit) y abridlo otra vez. Después:

```powershell
docker run --rm hello-world        # debe decir "Hello from Docker!"
```

### 1.3. kubectl, kind y Python

```powershell
winget install -e --id Kubernetes.kubectl
winget install -e --id Kubernetes.kind
winget install -e --id Python.Python.3.12
```

Cerrad y abrid PowerShell para que se carguen los nuevos `PATH`.

No hace falta instalar JMeter, Java ni Maven para el lab: JMeter va dentro de
un contenedor y el JAR se puede compilar con Docker (paso 3).

## 2. Descargar el código

```powershell
Set-Location $HOME
git clone https://github.com/dasuarezang/pae-itnow-synthetic-traffic.git
Set-Location pae-itnow-synthetic-traffic
git checkout CodigoLimpio
$repo = (Get-Location).Path
```

## 3. Compilar el servidor (bank-api-stress.jar)

Con Docker, sin instalar Java ni Maven (la primera vez descarga la imagen de
Maven):

```powershell
Set-Location "$repo\backend-java"
docker run --rm -v "${PWD}:/app" -w /app maven:3.9-eclipse-temurin-21 mvn -q -B -DskipTests package
Get-Item target\bank-api-stress.jar     # unos 26 MB
```

El lab lo encuentra solo en `..\backend-java\target\bank-api-stress.jar`, y la
base de datos de partida en `..\backend-java\db\reset.sql`.

## 4. Generar el plan con MORFEO (bloque Python)

```powershell
Set-Location "$repo\morfeo-python"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1          # si PowerShell lo bloquea: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install -e .
morfeo generate --jar ..\backend-java\target\bank-api-stress.jar --logs trafico.json --out-dir build --tz Europe/Madrid
deactivate
```

`trafico.json` es el log real de 24 h (16 589 peticiones) que está en la rama.
Debe acabar con la validación en "correcto" y `Plan escrito en ...\build\plan.jmx`.

### 4.1. El plan de la demo: 10 minutos

**La demo dura 10 minutos.** El `plan.jmx` que escribe `generate` reproduce
las 24 horas del día a ritmo real (un minuto por hora, 26 min en total) y
apenas mete carga: entre 0 y 9 sesiones por minuto, casi nada que ver en
Grafana. Para la demo se genera, con el mismo modelo, un plan de las horas
punta de la mañana (8 a 11), dos minutos por hora y 20 veces la intensidad
real:

```powershell
.\.venv\Scripts\Activate.ps1
morfeo jmeter build\profile.json build\endpoints.json --requests build\requests.csv `
    -o "$repo\morfeo-lab\scenarios\plan-demo-10min.jmx" --hours 8-11 --factor 20 --slot-seconds 120
deactivate
```

Resultado esperado: 4 grupos (hora 08 a hora 11) con 80, 135, 110 y 104 hilos,
y `Duración máxima de la prueba: 624 s` (10 min 24 s, más unos segundos de
alta de los 199 usuarios del pool).

Si queréis el día completo a ritmo real (26 min), copiad también
`build\plan.jmx` a `$repo\morfeo-lab\scenarios\`. Para la demo, dejad solo el
de 10 minutos en `scenarios\`.

En PowerShell las líneas se continúan con el acento grave `` ` `` al final.

**Plan de reserva:** la rama ya trae `morfeo-lab\scenarios\plan-demo-10min.jmx`,
generado con este mismo comando y probado en el lab (es el del resultado del
paso 6). Si el día de la demo falla el paso 4, se puede usar tal cual; el
comando de arriba lo sobrescribe con uno equivalente.

## 5. Preparar el lab

```powershell
Set-Location "$repo\morfeo-lab"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -e .
morfeo-lab doctor
```

`doctor` no cambia nada: comprueba la máquina y dice qué falta. Todo debe salir
con `√` y acabar en **This machine is ready**.

> Usad el comando `morfeo-lab` del entorno virtual y no `.\morfeo-lab.cmd`: el
> `.cmd` llama a `py`/`python` del sistema, que puede no tener las
> dependencias instaladas.

## 6. Montar el lab, desplegar el servidor y lanzar el plan

```powershell
morfeo-lab up                         # clúster, imágenes, Prometheus y Grafana (la 1.ª vez ~15 min)
morfeo-lab deploy bank-api            # 1 instancia de bank-api + PostgreSQL
morfeo-lab validate scenarios\        # el plan es válido (no necesita clúster)
morfeo-lab run scenarios\plan-demo-10min.jmx --generators 1 --tag demo
```

`run` escribe en la consola el enlace de Grafana de esa prueba (`Live view`) y
una barra de progreso con peticiones por segundo, p95 y errores. `Ctrl+C` la
para limpiamente y conserva los resultados parciales.

### Resultado esperado

Lo que dio esta misma prueba el 2026-10-15 (Windows 11, Docker Desktop 4.95
con WSL limitado a 12 GB, run `20261015-131657-demo-10min`):

```
   Passed   MORFEO: tráfico sintético
    3,332 requests · 5.4 req/s · p95 7.0 ms · p99 27.0 ms · server errors 0.00%

    Acceptance criteria
      √ Server errors                         0.000%  target ≤ 1.00%
      √ p95 latency                           7.0 ms  target ≤ 300 ms
      √ p99 latency                          27.0 ms  target ≤ 1,000 ms
      √ /api/saldo/{id} p95 latency           4.0 ms  target ≤ 150 ms
      √ No application restarts                    0  target 0
      √ setUp completed              0 of 199 failed  target no failed requests
      √ Traffic log complete                  100.0%  target ≥ 98% of the answered requests
      √ Load generator CPU                       25%  target < 90% of 2.00 cores
```

| Endpoint | Peticiones | p95 | Errores de servidor |
|---|---|---|---|
| `GET /api/saldo/{id}` | 1 783 | 4 ms | 0 |
| `POST /api/retirada` | 787 | 11 ms | 0 (34 respuestas 400) |
| `POST /api/ingreso` | 716 | 15 ms | 0 |
| `POST /api/usuario/crear` | 46 | 7 ms | 0 |

Cómo leerlo:

- **Passed** y **server errors 0.00 %**: la única instancia de bank-api
  aguanta la mañana con 20 veces el tráfico real sin un solo 5xx.
- Las 34 respuestas **400** de `retirada` son retiradas sin fondos: es
  comportamiento del cliente, previsto, y no cuenta como fallo.
- **Traffic log complete 100 %**: el servidor registró exactamente las 3 332
  peticiones que JMeter recibió. Ninguna se perdió por el camino.
- **setUp 0 of 199 failed**: el alta de los 199 usuarios del pool, antes de
  la carga, fue bien.
- La proporción de endpoints (53 % saldo, 24 % retirada, 21 % ingreso) es la
  del modelo aprendido de los logs.
- Los números exactos varían un poco entre ejecuciones (el plan es
  aleatorio), pero el veredicto debe ser el mismo.

En la misma prueba, con WSL limitado a 12 GB, el PC estuvo en torno al 35 %
de memoria (64 GB de RAM).

## 7. Ver los resultados y Grafana

```powershell
morfeo-lab open                       # informe de la última ejecución (HTML)
morfeo-lab open grafana               # o abrid http://localhost:3000
```

Grafana entra sin usuario ni contraseña. Mientras corre `run` se ve en directo
la carga pedida y la real, la latencia, los errores, la CPU y la memoria del
servidor, los hilos web y las conexiones a la base de datos.

## 8. Apagar

```powershell
morfeo-lab down                       # quita el servidor y JMeter; el clúster queda para la próxima
morfeo-lab down --destroy             # borra todo el lab (el próximo up vuelve a tardar)
```

Docker Desktop puede cerrarse después (clic derecho en la ballena → Quit).

## 9. Si algo falla

| Síntoma | Causa y solución |
|---|---|
| `wsl --status` dice "unable to start since virtualisation is not enabled" | La virtualización está desactivada en la BIOS (1.1), o Windows aún no ha terminado de instalar la Plataforma de Máquina Virtual: reiniciad con "Actualizar y reiniciar" y esperad |
| Docker Desktop se queda en "Starting" / `docker` da error 500 | Igual que la fila anterior. Si `wsl --status` ya está bien, cerrad Docker Desktop (Quit) y abridlo otra vez |
| `docker: installed but not running` en `doctor` | Docker Desktop no está abierto o aún no dice "Engine running" |
| `Some lab files have Windows line endings (CRLF)` | El repositorio se descargó sin respetar `.gitattributes` (p. ej. como ZIP). Clonad con `git clone`; si ya está clonado: `git rm -r --cached morfeo-lab; git reset --hard` (perdéis cambios sin guardar) |
| `ModuleNotFoundError: No module named 'yaml'` | Se ha usado `.\morfeo-lab.cmd` o un Python sin dependencias. Activad el `.venv` del lab y usad `morfeo-lab` |
| `bank-api not ready: JAR not found` | Falta el paso 3 |
| La memoria del PC llega al 100 % con el lab en marcha | Falta el `.wslconfig` del paso 1.2, o se creó con Docker abierto: cerrad Docker Desktop, `wsl --shutdown` y abridlo otra vez |
| Se borró el clúster (o Docker se reinició) a mitad de una prueba | `run` se queda esperando: paradlo con `Ctrl+C`, y `morfeo-lab up` + `morfeo-lab deploy bank-api` lo vuelven a montar (las imágenes ya están: tarda unos minutos, no los 15 de la primera vez) |
| `Port 3000 (grafana) is in use` | Otro programa usa el 3000. Cambiad `cluster.ports.grafana` en `config/lab.yaml` |
