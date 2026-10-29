# morfeo jmeter: plan de JMeter a partir del perfil de MORFEO

`morfeo jmeter` lee las salidas de MORFEO, `profile.json` (de `morfeo train`),
`endpoints.json` (de `morfeo scan`) y, si se le da, `requests.csv` (de `morfeo
analyze`), y escribe un plan de JMeter 5.6.3 (`.jmx`) que reproduce contra la API
el comportamiento de usuario aprendido. `morfeo generate` lo ejecuta al final
con las opciones por defecto y deja `plan.jmx` junto a los demás ficheros
(`--no-jmx` lo omite). Vive en `src/morfeo/jmeter_plan/` y solo usa la
biblioteca estándar de Python.

Todos los parámetros del plan (modo, carga, hilos, usuarios, importes...) salen
de `Options` (`plan.py`), que hoy rellenan los flags y las estadísticas del
perfil. Es el único sitio que habría que alimentar para que otro componente (la
IA que se prevé más adelante) decida esos parámetros.

El plan es un solo fichero autocontenido y solo usa elementos de JMeter y Groovy
(JSR223), sin plugins. Con las mismas entradas y opciones, el `.jmx` sale
idéntico byte a byte.

## Qué hace el plan

- **Una iteración de un hilo es una sesión.** El *HTTP Cookie Manager* se vacía
  en cada iteración y la opción "Same user on each iteration" está desactivada,
  así que cada sesión recibe su propia cookie `MORFEO_SID` y en `trafico.json`
  las sesiones no se mezclan.
- **Orden de las peticiones.** Sigue la cadena de Markov de `profile.json`. La
  primera petición sale de la fila `START`, y cada petición siguiente se sortea
  en la fila del estado que se acaba de ejecutar, hasta llegar a `END` o al
  máximo de pasos.
- **Esperas entre peticiones.** Se sortean de `think_time_ms.by_state[estado
  anterior]` o, si ese estado no tiene tabla, de `think_time_ms.global`. Se usa
  la inversión de los 101 percentiles con interpolación lineal, igual que
  `sample_from_dist` de MORFEO. La espera cuenta desde el fin de la petición
  anterior hasta el inicio de la siguiente, como en el perfil. La primera
  petición de la sesión no espera.
- **Usuarios.** Con `--requests requests.csv`, el pool son los clientes más
  activos de los logs (hasta `--pool-size`, máximo 1.000) y cada sesión elige uno
  con la frecuencia con que aparece en ellos: unos pocos muy activos y muchos
  ocasionales, como en los logs. Sin `requests.csv`, un pool inventado
  (`mjpool_p0001`, `mjpool_p0002`, ...). Un *setUp Thread Group* los da de alta
  con el endpoint de alta antes de que empiecen las sesiones (409 si ya
  existen). Si la sesión llega al endpoint de alta, pasa a un id nuevo
  (`<prefijo>_t<grupo>-<hilo>_<n>`, con el prefijo de la ejecución), lo da de
  alta y lo usa hasta el final. Un pool dado de alta una vez sirve para las
  ejecuciones siguientes mientras no se vacíe la base de datos.
- **Carga.** Por defecto reproduce la curva por hora del perfil con su
  intensidad real (sesiones por hora), con cada hora comprimida en 60 segundos.
  Con `--mode steps` hace una escalera de múltiplos de la hora pico para buscar
  la saturación. Las sesiones de cada Thread Group llegan como un único proceso
  de Poisson, que se reparten los hilos del grupo.
- **Aciertos y fallos.** Una respuesta 2xx, 3xx o 4xx es un acierto: en MORFEO
  los 4xx (por ejemplo, fondos insuficientes) son uso normal y van en
  `error_rate`. Un 5xx es un fallo (`server_error_rate`, por ejemplo un 503
  cuando se agota el pool de la base de datos). También es fallo no recibir
  respuesta (conexión rechazada o tiempo agotado).
- **Resumen al final.** Un *tearDown Thread Group* escribe en la consola y en
  `jmeter.log` una línea por Thread Group con las sesiones que ha empezado, los
  retrasos y las llegadas que no encontraron un hilo libre.

## Cómo se ejecuta

```bash
morfeo jmeter build/profile.json build/endpoints.json --requests build/requests.csv -o plan.jmx
```

Salida con el perfil de la prueba corta del backend:

```
Endpoint de alta: POST /api/usuario/crear (detectado automáticamente)
Estados con petición (4): GET /api/saldo/{id}, POST /api/ingreso, POST /api/retirada, POST /api/usuario/crear
Horas sin sesiones en el perfil, no ocupan tiempo en la prueba: 0-9, 11-23
Grupo                 Sesiones/h  Sesiones  Sesión s  Hilos  Ventana s  Retardo s  Duración s
---------------------------------------------------------------------------------------------
hora 10                    156.0       2.6       3.0      2         60          0          90
Plan escrito en plan.jmx. Duración máxima de la prueba: 90 s, más el alta del pool.
Alta del pool: 200 usuarios de los logs, elegidos con su frecuencia (user_001 a user_200, de build/requests.csv) en el setUp del plan. Esas altas quedan en trafico.json como sesiones de altas seguidas. Antes de pasar el log a MORFEO, quítalas con:
  awk '!($0 ~ "\"method\":\"POST\",\"path\":\"/api/usuario/crear\",\"status\":[0-9]+,\"latency_ms\":[0-9.]+,\"client_id\":\"" && $0 !~ "\"client_id\":\"[^\"]*_t[0-9]+-[0-9]+_[0-9]+\",")' trafico.json > trafico-sin-pool.json
O da de alta el pool aparte con --pool-only y ejecuta este plan con -Jcreate_pool=false.
```

Cada fila de la tabla es un Thread Group: sesiones por hora, sesiones previstas
en la ventana, duración de sesión con la que se calculan los hilos, hilos,
ventana en la que empiezan sesiones, retardo de arranque y duración total del
grupo (ventana más margen). Los avisos salen por la salida de error con el
prefijo `Aviso:`. Las últimas líneas explican qué hacer con las altas del pool
(ver "Validar el tráfico generado con MORFEO").

Para la API del banco conviene fijar los importes como los elige
`simulador-usuarios` (ingresos con mediana 80 y alta con mediana 300):

```bash
morfeo jmeter profile.json endpoints.json -o plan.jmx \
  --number cantidad=lognormal:80:0.8 --number saldo_inicial=lognormal:300:1.0
```

## Opciones

| Opción | Por defecto | Uso |
|---|---|---|
| `profile` | | `profile.json` de `morfeo train` |
| `endpoints` | | `endpoints.json` de `morfeo scan` |
| `-o`, `--out` | `plan.jmx` | fichero del plan |
| `--mode` | `curve` | `curve`: curva por hora del perfil. `steps`: escalera de carga |
| `--slot-seconds` | `60` | segundos de prueba por cada hora de la curva (`curve`) |
| `--factor` | `1.0` | multiplica las sesiones por hora, en los dos modos |
| `--hours` | todas | horas de la curva: `8-20`, `8,9,10`, `22-2` (cruza la medianoche). Se recorren en el orden escrito |
| `--base-rate` | hora pico | sesiones por hora del escalón x1 (`steps`) |
| `--steps` | `1,2,5,10` | multiplicadores de la escalera (`steps`) |
| `--step-seconds` | `300` | segundos de cada escalón (`steps`) |
| `--tail-seconds` | automático | margen tras la ventana para que acaben las sesiones empezadas. Por defecto, el mayor de 30 s, 3 veces la duración media de sesión y su p99 |
| `--max-threads` | `500` | tope de hilos por Thread Group. Si se alcanza, sale un aviso con las sesiones por hora que caben |
| `--thread-factor` | `2.0` | hilos por cada sesión simultánea de la ley de Little, más uno. Ver "Hilos" |
| `--latency-margin` | `5` en `steps`, `0` en `curve` | segundos que se suman por petición a la duración de sesión para calcular los hilos. Ver "Hilos" |
| `--requests` | | `requests.csv` de `morfeo analyze`: los usuarios del pool salen de los logs |
| `--pool-size` | `200` | usuarios del pool: los más activos de `--requests` (máximo 1.000) o inventados |
| `--pool-only` | | escribe un plan que solo da de alta el pool. Ver "Validar el tráfico generado con MORFEO" |
| `--create-endpoint` | automático | endpoint de alta, por ejemplo `'POST /api/usuario/crear'`, o `none` para no dar de alta a nadie |
| `--id-field` | `id` | nombre del campo del cuerpo, de la variable de ruta o del parámetro que lleva el usuario |
| `--number CAMPO=DIST` | `lognormal:100:1.0` | distribución de un campo numérico del cuerpo: `lognormal:MEDIANA:SIGMA` (mínimo 1) o `uniform:MIN:MAX`. Se puede repetir. `*` cambia la distribución por defecto |
| `--max-steps` | 3 veces la sesión más larga, mínimo 50 | peticiones máximas por sesión |
| `--skip-unmatched` | | no reproducir las rutas de los logs que no están en el jar (por defecto se reproducen, como los bots) |
| `--no-balance` | | importes de retirada sorteados sin mirar el saldo visto |
| `--spend-known` | `1 - error_rate` | probabilidad de que una retirada quepa en el saldo visto |
| `--spend-fraction` | `0.05:0.55` | parte del saldo visto que se retira |
| `--seed` | `42` | semilla de los sorteos. Se puede cambiar al ejecutar con `-Jseed` |

El endpoint de alta se detecta solo: el primer `POST` cuya ruta contiene
`crear`, `create`, `register` o `signup` y cuyo cuerpo tiene el campo id. El
conversor dice cuál ha elegido y si había otros candidatos. El alta se envía
con el método del endpoint (`POST` si es `ANY`).

## Ejecutar el plan con JMeter (modo no gráfico)

```bash
jmeter -n -t plan.jmx -l results.jtl -j jmeter.log \
  -Jhost=localhost -Jport=7070 \
  -Lorg.apache.jmeter.protocol.http.sampler.HTTPSamplerBase=ERROR
```

- `-l results.jtl` guarda una línea por petición. Con `-e -o informe/` JMeter
  genera además el informe HTML al terminar.
- La etiqueta de cada petición es el estado del perfil (`GET /api/saldo/{id}`),
  así que los resultados se comparan directamente con `profile.json`. Las
  altas del pool salen como `alta pool POST /api/usuario/crear`.
- La opción `-L...HTTPSamplerBase=ERROR` no es obligatoria. JMeter 5.6.3 escribe
  en `jmeter.log` un aviso "Existing CookieManager ... superseded" por cada
  petición cuando hay un *Cookie Manager*, aunque las cookies funcionen bien
  (comprobado: una cookie por sesión). En una prueba larga el log crece mucho.
  Esta opción solo oculta los avisos de esa clase.
- Antes de un experimento conviene vaciar la base de datos con `db/reset.sql`
  (ver `backend-java/README.md`) y volver a dar de alta el pool. Si no se
  vacía, el pool de la ejecución anterior se reutiliza (las altas repetidas dan
  409, que vale) con los saldos que dejó, y los ids de las altas en sesión no
  chocan porque el prefijo de la ejecución cambia cada vez.
- Al terminar, el plan escribe una línea por Thread Group en la consola y en
  `jmeter.log`, por ejemplo:

  ```
  MORFEO: escalón 1 (x1): 7200.0 sesiones/h: 365 llegadas en la ventana de 180 s (media prevista 360.0); 365 sesiones empezadas, 7300.0 por hora (pedidas 7200.0); 0 con más de 1 s de retraso, retraso medio 0.00 s, máximo 0.0 s; 0 sin empezar por falta de hilos libres
  ```

  Antes de leer la saturación en una escalera conviene comprobar en estas
  líneas que cada escalón ha empezado las sesiones pedidas. "Sin empezar por
  falta de hilos libres" quiere decir que todos los hilos del grupo estaban
  ocupados en sesiones más largas de lo previsto: la carga real de ese escalón
  fue menor que su etiqueta. Se arregla subiendo `--latency-margin`,
  `--thread-factor` o `--max-threads`.

### Propiedades que se pueden cambiar al ejecutar

| Propiedad | Por defecto | Uso |
|---|---|---|
| `protocol` | `http` | `http` o `https` |
| `host` | `localhost` | servidor de la API |
| `port` | `7070` | puerto de la API |
| `run_prefix` | `jm` y la fecha y hora de arranque, `jm20261013093210` | prefijo de los ids de las altas en sesión. Solo letras, cifras, `_` o `-` |
| `pool_prefix` | `mjpool` | prefijo de los ids del pool. Otro valor da otro pool. Solo letras, cifras, `_` o `-` |
| `create_pool` | `true` | con `false`, el setUp no da de alta el pool (ya existe) |
| `seed` | el de `--seed` | semilla de los sorteos |
| `connect_timeout` | `5000` | ms para abrir la conexión |
| `response_timeout` | `60000` | ms de espera de la respuesta |

## Validar el tráfico generado con MORFEO

El setUp da de alta el pool a través de la API, como cualquier cliente, así
que el backend escribe esas altas en `trafico.json`. Cada hilo del setUp manda
todas sus altas con la misma cookie: con el pool por defecto son 4 sesiones de
50 altas seguidas, casi sin espera entre ellas. Para la prueba de carga no
importan, porque acaban antes de que empiecen las sesiones. Pero si se pasa
ese `trafico.json` a `morfeo analyze` y `morfeo train`, cuentan como sesiones de
usuario: añaden transiciones de alta a alta, que el perfil no tiene, y esperas
casi nulas después de un alta. Las altas son raras en el perfil, así que estas
sesiones dominan la fila del alta aunque la prueba tenga miles de sesiones.
Hay dos formas de dejarlas fuera.

**Quitarlas del log.** El conversor imprime la orden exacta. Quita las líneas
del endpoint de alta cuyo cliente no es un id creado dentro de una sesión (esos
acaban en `_t<grupo>-<hilo>_<n>`): todas las demás altas son del pool, sean
usuarios de los logs o `mjpool_p0001`:

```bash
awk '!($0 ~ "\"method\":\"POST\",\"path\":\"/api/usuario/crear\",\"status\":[0-9]+,\"latency_ms\":[0-9.]+,\"client_id\":\"" && $0 !~ "\"client_id\":\"[^\"]*_t[0-9]+-[0-9]+_[0-9]+\",")' \
  trafico.json > trafico-sin-pool.json
morfeo analyze trafico-sin-pool.json --endpoints endpoints.json -o analisis
```

Ninguna sesión del plan da de alta un id del pool (las altas en sesión usan
`_t`), así que el resto del log queda intacto y las sesiones del setUp
desaparecen enteras. Si se ejecutó con otro `-Jpool_prefix`, hay que cambiar
`mjpool` en la orden.

**Dar de alta el pool aparte.** Con `--pool-only` el conversor escribe un plan
que solo da de alta el pool. Se ejecuta antes de empezar a medir, y el plan de
tráfico se ejecuta con `-Jcreate_pool=false`:

```bash
morfeo jmeter profile.json endpoints.json --requests requests.csv -o pool.jmx --pool-only
morfeo jmeter profile.json endpoints.json --requests requests.csv -o plan.jmx
jmeter -n -t pool.jmx -l pool.jtl -Jhost=... -Jport=...
sleep 1
n=$(wc -l < trafico.json)
jmeter -n -t plan.jmx -l results.jtl -Jhost=... -Jport=... -Jcreate_pool=false
sleep 1
tail -n +$((n + 1)) trafico.json > trafico-prueba.json
```

El backend tiene `trafico.json` abierto mientras corre, así que mover el
fichero entre las dos ejecuciones no sirve: seguiría escribiendo en el fichero
movido. Por eso se cuentan las líneas antes de la prueba y se corta después.
Cuando no le llegan peticiones, el backend vuelca el log en menos de medio
segundo; de ahí el `sleep 1`. El pool queda en la base de datos: mientras no
se vacíe, las ejecuciones siguientes solo necesitan `-Jcreate_pool=false`.
Después de `db/reset.sql` hay que ejecutar `pool.jmx` otra vez.

## Cómo cambia el laboratorio el destino

El plan no lleva el servidor dentro: lo toma de las propiedades al arrancar.
Basta con pasar `-J` en la línea de comandos:

```bash
jmeter -n -t plan.jmx -l results.jtl -Jhost=10.20.0.15 -Jport=8080
```

o un fichero de propiedades con `-q`:

```properties
# laboratorio.properties
host=bank-api.lab.local
port=80
protocol=http
response_timeout=30000
```

```bash
jmeter -n -t plan.jmx -l results.jtl -q laboratorio.properties
```

En modo distribuido (`-R` con varios servidores de JMeter), las propiedades se
pasan a los servidores con `-G` (`-Ghost=... -Gport=...`). Cada servidor ejecuta
el plan entero, así que la carga se multiplica por el número de servidores: hay
que generar el plan con `--factor` dividido entre ese número. Cada servidor
sortea sus propias sesiones y llegadas: antes de cada prueba, JMeter guarda en
el servidor su `host:puerto` (propiedad interna `__jm.D_TG`), y el plan lo
mezcla con la semilla. También lo añade al prefijo de las altas en sesión
(`jm20261013093210_10-20-0-15-1099_t1-3_1`), así que dos servidores nunca dan de
alta el mismo id aunque arranquen en el mismo segundo. Con la misma semilla,
cada servidor repite sus sorteos de una ejecución a otra. Los usuarios del pool
son los mismos en todos los servidores: cada servidor ejecuta el setUp, y las
altas que ya hizo otro dan 409, que vale.

## Cómo está hecho el plan

```
Test Plan            variables morfeo_modelo, morfeo_prefijo, morfeo_prefijo_pool, morfeo_semilla
  HTTP Request Defaults    protocol, host y port de las propiedades
  HTTP Cookie Manager      se vacía en cada iteración
  setUp Thread Group       alta del pool: 4 hilos (o menos si el pool es menor)
    Flow Control Action    primer id de cada hilo (nada si create_pool=false)
    While Controller       mientras queden ids
      HTTP Request         alta, con aserción 201 o 409
  Thread Group por hora (o por escalón)
    Response Assertion     2xx, 3xx y 4xx valen; 5xx es fallo
    Flow Control Action    inicio de sesión (no genera muestra)
      JSR223 PreProcessor  usuario del pool y primer estado (fila START)
      JSR223 Timer         espera hasta la llegada de la sesión (Poisson del grupo)
    While Controller       mientras morfeo_seguir sea true
      JSR223 Timer         tiempo de espera según el estado anterior
      JSR223 PostProcessor siguiente estado (Markov)
      Switch Controller    petición del estado actual, por índice
        HTTP Request       un sampler por estado del perfil
  tearDown Thread Group    resumen de llegadas por grupo (no envía peticiones)
```

- **El modelo.** Va en la variable `morfeo_modelo` del Test Plan como JSON en
  una línea: nombres de los estados, filas de la matriz como destinos y
  probabilidad acumulada, tablas de percentiles de espera, máximo de pasos,
  tamaño del pool y distribuciones de los campos numéricos. El primer script
  que lo necesita lo interpreta y lo guarda en las propiedades de JMeter para
  todos los hilos.
- **El Switch Controller elige por índice**, no por nombre, porque los nombres
  de estado llevan espacios y llaves. El hijo número `i` es el estado `i` de
  `morfeo_modelo.estados`.
- **Peticiones.** Las variables de ruta que se llaman como el campo id llevan el
  usuario de la sesión (`/api/saldo/${morfeo_usuario}`). El resto de variables
  de ruta toman el ejemplo del parámetro, su valor por defecto o `1`. Los
  parámetros de query, de cabecera y de formulario toman el ejemplo o el valor
  por defecto; los opcionales sin ninguno de los dos no se envían, y los
  obligatorios sin valor llevan el primer valor del `enum`, `true` si son
  booleanos o `1`. Las peticiones con cuerpo JSON llevan
  `Content-Type: application/json`.
- **Cuerpos.** Salen de `body.example` y de su esquema. El campo id del primer
  nivel pasa al usuario de la sesión. Cada campo numérico se sortea antes de la
  petición: con `--number` si se da; si no, uniforme entre el `min` y el `max`
  del esquema; si no, lognormal con la mediana de un saldo inicial (300, σ 1,0)
  si el nombre del campo lo es (`saldo_inicial`, `initial`, `balance`...) o la
  de un movimiento (80, σ 0,8), nunca por debajo del `min` del esquema (1 para
  enteros, 0,01 para decimales positivos). Los textos con `enum`, `uuid`,
  `email`, `date`, `date-time` o longitud mínima/máxima se sortean también; los
  demás se copian del ejemplo.
- **Saldo y retiradas.** Cada respuesta con `"saldo"` o `"saldo_actual"` actualiza
  el saldo que la sesión ha visto (se borra al empezar la sesión). El importe de
  una retirada (un POST cuya ruta contiene `retir`, `withdraw`, `debit`,
  `charge` o `cargo`) es, con probabilidad `1 - error_rate` del endpoint en el
  perfil, una parte del saldo visto (de `--spend-fraction`, 5 % a 55 % por
  defecto); en el resto, un importe sorteado que puede no tener fondos. Así la
  proporción de 400 por falta de fondos sale de los logs y no de una constante.
  `--no-balance` lo desactiva.
- **Rutas sin endpoint.** Un estado que el perfil marca como `matched: false`
  (rutas de los logs que no están en el jar, como los bots que piden
  `/wp-admin`) se reproduce como una petición sin parámetros ni cuerpo, con aviso;
  el servidor responde 404, que cuenta como acierto. `--skip-unmatched` los
  quita. Un estado que no está en `endpoints.json` y tampoco es `matched: false`
  se descarta: sus transiciones se quitan, la fila se renormaliza y sale un
  aviso. `morfeo train` excluye por defecto las rutas sin endpoint; con
  `--keep-unmatched` entran en el perfil y, por tanto, en el plan.
- **Llegadas.** Cada Thread Group tiene un solo proceso de Poisson de tasa R,
  guardado en las propiedades de JMeter y compartido por sus N hilos. El hilo
  que queda libre toma la siguiente llegada prevista y espera hasta su hora.
  Si esa hora ya ha pasado porque todos los hilos estaban ocupados, la sesión
  empieza en el acto y el retraso queda anotado. La ventana empieza cuando el
  primer hilo del grupo toma su primera llegada. Ninguna sesión empieza
  después de su ventana: una
  llegada que no ha encontrado hilo libre antes de que acabe la ventana se
  descarta y se cuenta en el resumen. Las sesiones en marcha acaban dentro del
  margen.
- **Hilos.** `N = ceil(R / 3600 * D * f) + 1`, con R en sesiones por hora y `f`
  el `--thread-factor` (2 por defecto). D es la duración de sesión con la que
  se dimensiona: la duración media medida más `--latency-margin` segundos por
  cada petición de la sesión. La duración medida es la de la hora
  (`hourly_curve.hours[].mean_session_duration_s`) si está, si no la global
  (`session_length.duration_s.mean`) y, si tampoco está, una estimación con las
  peticiones por sesión, las esperas y las latencias. En la escalera el margen
  es de 5 s por petición, lo que espera el backend por una conexión de la base
  de datos antes de responder 503 (`DB_TIMEOUT_MS`). Así, cuando el servidor se
  satura y las sesiones se alargan, sigue habiendo hilos libres y cada escalón
  mantiene su tasa. Sin margen, la tasa quedaría limitada a unas N / D' sesiones
  por segundo, con D' la duración real, justo cuando se busca la saturación. En
  la curva el margen es 0. El total tiene el tope de `--max-threads`.

## Cambios respecto al diseño pedido

1. **Paso de inicio de sesión.** Es un *Flow Control Action* en pausa 0 con un
   JSR223 PreProcessor y el JSR223 Timer de llegadas como hijos. No genera
   ninguna muestra, así que no aparece en `results.jtl` ni en el informe.
2. **Dónde va el modelo.** El JSON va en una variable del Test Plan, no dentro
   de cada script, para no repetirlo en cada elemento. Los `$` del JSON se
   escriben como `\u0024` (el escape de JSON del dólar) para que JMeter no
   los interprete.
3. **Id de las altas en sesión.** Es `<prefijo>_t<grupo>-<hilo>_<n>`: lleva
   también el número de Thread Group, porque el número de hilo se repite entre
   grupos. En modo distribuido el prefijo lleva además el servidor.
4. **Llegadas compartidas por grupo.** El diseño pedía un proceso de Poisson de
   tasa R/N por hilo. Así, un hilo que sigue con una sesión empieza la
   siguiente tarde, y cada hilo se comporta como una cola de un solo servidor.
   Con el perfil de la prueba de extremo a extremo (sesiones de 31 s de media)
   un 34 % de las sesiones empezaba tarde, 19 s de media, y un 12 % después de su
   escalón. Con un solo proceso por grupo los hilos forman una cola de varios
   servidores y, con los mismos hilos, ninguna sesión empezó más de 1 s tarde
   (ver "Comprobaciones hechas").
5. **Ninguna sesión empieza fuera de su ventana.** Si una llegada no encuentra
   hilo libre antes del final de su ventana, se descarta y se cuenta, en vez
   de empezar en la ventana del grupo siguiente.
6. **Margen de latencia en la escalera.** Los hilos de `--mode steps` se
   calculan con 5 s más por petición (`--latency-margin`), para que la tasa no
   caiga al saturarse el servidor.
7. **Prefijo fijo del pool.** Los ids del pool son `mjpool_p0001`, ..., no
   `<run_prefix>_p0001`. Así el pool se puede dar de alta una vez, aparte
   (`--pool-only`), y las ejecuciones siguientes lo usan con
   `-Jcreate_pool=false`.
8. **Resumen de llegadas.** Un *tearDown Thread Group* con un script, sin
   peticiones, escribe el resumen de cada grupo.
9. **Horas sin sesiones.** No ocupan tiempo: la hora siguiente con sesiones
   empieza justo después. Se listan al generar el plan.
10. **`--factor`** también se aplica en el modo `steps`.
11. **Duración de sesión para calcular hilos.** Antes de la estimación con
    peticiones, esperas y latencias se usa la duración media global del perfil,
    que es un dato medido.
12. **Margen por defecto.** Es el mayor de 30 s, 3 veces la duración media de
    sesión y el p99 de esa duración.
13. **Opciones y propiedades añadidas.** `--thread-factor`, `--latency-margin`,
    `--pool-only`, `--max-steps` y las propiedades `pool_prefix`, `create_pool`,
    `seed`, `connect_timeout` y `response_timeout`.
14. **Conexiones.** Con "Same user on each iteration" desactivado, JMeter cierra
    también las conexiones al empezar cada sesión. Un usuario nuevo abre una
    conexión nueva, como en el uso real.
15. **Redirecciones.** No se siguen: un 3xx queda registrado como 3xx.
16. **Aserción del setUp.** En el alta del pool solo valen 201 y 409. Cualquier
    otro código es un fallo, porque el pool tiene que existir.

## Límites conocidos

- **Llegadas sin hilo libre.** Si las sesiones duran mucho más que la duración
  con la que se calcularon los hilos (en la curva, o en la escalera con un
  servidor que tarda más de 5 s por petición), todos los hilos pueden quedar
  ocupados. Entonces las sesiones empiezan tarde y, al final de la ventana, las
  llegadas pendientes se descartan. El resumen final lo dice para cada grupo.
- **Semilla fija.** Con la misma semilla las llegadas de cada grupo y los
  sorteos de cada hilo se repiten en cada ejecución (qué hilo toma cada llegada
  depende de los tiempos). Para comparar ejecuciones independientes hay que
  cambiar `-Jseed`.
- **Cuerpos.** Los logs no guardan cuerpos ni valores, así que los importes y
  los textos salen del esquema y de medianas por defecto, no de datos medidos.
  Los números se sortean por nombre de campo, igual en todos los endpoints
  (`cantidad` tiene la misma distribución en ingresos y retiradas). Si se
  conocen los importes reales, se fijan con `--number`.
- **Pool.** Cada sesión elige un usuario con reemplazo (con la frecuencia de los
  logs si hay `--requests`): dos sesiones simultáneas pueden usar el mismo
  usuario. Los saldos del pool cambian de una ejecución a otra si no se vacía la
  base de datos. Los usuarios del pool se crean con el saldo inicial sorteado, no
  con el que tenían en los logs. Sin endpoint de alta el pool no se crea y las
  peticiones a esos ids darán 404, que cuentan como acierto.
- **Filtro del pool.** La orden de `awk` quita las altas del endpoint de alta
  cuyo cliente no acaba en `_t<grupo>-<hilo>_<n>`. Si un usuario de los logs se
  llamara así, sus altas del pool se conservarían.
- **Otros cuerpos y parámetros.** Un cuerpo que no es JSON se envía con el texto
  del ejemplo. Los parámetros de tipo cookie no se envían.
- **Modelo de primer orden.** La espera solo depende del estado anterior, y
  cada sesión tiene como mucho `--max-steps` peticiones.
- **Curva comprimida.** Cada hora dura `--slot-seconds` con la intensidad real,
  así que en una hora tranquila salen pocas sesiones (2,6 en la hora 10 del
  perfil de ejemplo). Para más sesiones por hora se sube `--slot-seconds` o
  `--factor`.
- **Tope de hilos.** Si un grupo llega a `--max-threads`, el aviso dice cuántas
  sesiones por hora caben con la duración de sesión de la tabla.
- **Consola.** Las líneas del resumen llevan acentos. En una terminal sin
  locale UTF-8 salen como `?`; en `jmeter.log` salen bien.

## Comprobaciones hechas

- `python -m pytest tests/jmeter_plan` (43 pruebas, 5 más con JMeter): XML bien formado con la
  estructura `hashTree`, modelo igual al perfil (filas que suman 1 y
  renormalización), hilos, márgenes de latencia y retardos de los dos modos,
  sustitución del usuario y de los números, parámetros opcionales, detección
  del endpoint de alta, plan `--pool-only`, la orden de `grep` aplicada a un log
  de ejemplo y salida determinista. Si encuentra JMeter (variables
  `JMETER_HOME` o `JMETER_BIN`, `jmeter` en el `PATH`, o
  `tools/apache-jmeter-5.6.3` junto al repositorio), ejecuta además seis planes cortos contra un
  puerto cerrado: los dos modos con su resumen, el pool aparte con
  `-Jcreate_pool=false`, sesiones más lentas de lo previsto con y sin margen de
  latencia, y la propiedad de servidor del modo distribuido. Si no, esas
  pruebas se saltan.
- Con JMeter 5.6.3 se cargó el plan con `SaveService` y se comprobó cómo
  interpreta JMeter cada propiedad (hilos, programador, cookies, While,
  Switch, cuerpos y aserciones).
- **Llegadas.** Perfil de la prueba de extremo a extremo (sesiones de 31 s de
  media), escalera de 7.200 y 14.400 sesiones por hora con escalones de 180 s,
  contra un puerto cerrado y con los mismos 126 y 250 hilos que daba el plan
  anterior (`--latency-margin 0`). El escalón 1 empezó 365 sesiones (7.300
  por hora) y el 2, 717 (14.340 por hora), ninguna con más de 1 s de retraso y
  ninguna sin empezar. Con el plan anterior, de un Poisson por hilo, fueron 309
  y 626 dentro de la ventana, y un 12 % y un 11 % después de ella. Con el margen
  por defecto (197 y 393 hilos) salieron las mismas cifras. La ventana empieza
  cuando el primer hilo toma su primera llegada, cerca de 1 s después de que
  arranquen los hilos (antes compila los scripts).
- **Servidor lento.** Un perfil que declara sesiones de 1 s cuando duran 4 s,
  a 36.000 sesiones por hora en un escalón de 20 s. Con el margen por defecto
  (221 hilos) empezaron las 220 llegadas sorteadas, todas dentro del escalón.
  El plan anterior (21 hilos) empezó 101 dentro y 57 después.
- **Alta del pool y validación.** Contra una imitación de la API (mismos
  endpoints, cookie `MORFEO_SID` y formato de `trafico.json` que el backend), con
  el perfil de la prueba corta multiplicado por 50 (7.800 sesiones por hora
  durante 180 s):
  - Con el pool aparte (`--pool-only` y después `-Jcreate_pool=false`), el
    `trafico.json` de la prueba tiene 385 sesiones, ninguna alta del pool y
    ningún 404. `morfeo validate` contra el perfil de entrada da distancia KS de
    las esperas 0,022, de la longitud de sesión 0,031 y variación total
    ponderada de las transiciones 0,037 (máximo por celda 0,042): las tres
    comprobaciones salen bien.
  - Con el plan por defecto, el `trafico.json` sin tocar tiene 389 sesiones: 4
    son las 200 altas del setUp, y 196 de las 355 transiciones que salen de un
    alta van a otra alta. `morfeo validate` da 0,191 en las esperas y 0,147 en
    las transiciones (máximo 0,546) y fallan esas dos comprobaciones. Después
    de la orden de `grep` quedan 385 sesiones, con 0,024, 0,031 y 0,042 (máximo
    0,046), y las tres salen bien.
  - La versión anterior de esta guía decía que `morfeo analyze` y `morfeo
    train` sobre el `trafico.json` generado daban la misma matriz. Esa
    comprobación usaba un log al que se le habían quitado a mano las 50 altas
    del pool, y la guía no lo decía. Las cifras de arriba la sustituyen.
- **Modo distribuido.** Dos `jmeter-server` en la misma máquina (puertos 1199
  y 1299) con `-R`, contra la imitación de la API. Los dos arrancaron en el
  mismo segundo, pero sus altas en sesión llevaron prefijos distintos
  (`jm20261013103532_127-0-0-1-1199` y `jm20261013103532_127-0-0-1-1299`): las
  15 dieron 201, ningún 409. Las altas del pool dieron 20 veces 201 y 20 veces
  409. Los 8 hilos de sesiones hicieron secuencias distintas en cada servidor,
  y en una segunda ejecución cada servidor repitió la suya. Con el plan
  anterior los dos servidores enviaban exactamente las mismas sesiones.
- La prueba de extremo a extremo contra el backend real la hace otra persona.
- **Scripts de Groovy.** Con Groovy 4.0.22 (el de JMeter 5.6.3) y objetos de JMeter
  simulados, todos los scripts compilan y se ejecutaron: el inicio de sesión
  elige usuarios con 80/10/10 % de un pool con esos pesos (3.194 / 417 / 389 de
  4.000), el saldo se lee de `saldo` y de `saldo_actual`, el 99,8 % de las
  retiradas con saldo conocido caben en él, los textos salen con el formato del
  esquema y el setUp usa los ids de la lista. No es una ejecución con JMeter:
  esas pruebas siguen pendientes.
- **Cadena completa con logs sintéticos.** `morfeo synth-logs` (por defecto en el
  formato `trafico.json` del backend) y `morfeo generate --jar bank-api-stress.jar`
  producen `plan.jmx` con los 200 usuarios más activos del log.
