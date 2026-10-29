# simulador-usuarios

Genera tráfico HTTP **real** contra `bank-api-stress`, imitando a usuarios. El
resultado es el `trafico.json` que escribe la propia API, listo para el
`log_analyzer` de MORFEO. Sustituye a los logs sintéticos (`morfeo synth-logs`)
cuando se quiere probar el pipeline con peticiones que de verdad han pasado por
la aplicación.

Java 21, sin dependencias: `java.net.http` e hilos virtuales (uno por sesión).

## Uso

```bash
# 1. Compilar y arrancar la API
cd backend-java && mvn clean package && java -jar target/bank-api-stress.jar

# 2. En otra terminal, compilar y lanzar el simulador
cd simulador-usuarios && mvn clean package
java -jar target/simulador-usuarios.jar --duracion 24h
```

Cada minuto escribe un resumen:

```
[11:31] sesiones: 107 iniciadas, 2 activas | peticiones: 335 | respuestas: {200=287, 201=40, 400=2, 404=6}
```

| Opción | Por defecto | Uso |
|--------|-------------|-----|
| `--url URL` | `http://localhost:7070` | API a la que se envía el tráfico |
| `--duracion D` | `1h` | tiempo lanzando sesiones nuevas: `90s`, `30m`, `2h`, `1d`. Al acabar espera hasta 2 min a las sesiones activas |
| `--sesiones-hora N` | `60` | sesiones por hora en la hora punta |
| `--usuarios N` | `200` | clientes distintos, incluidos `user_001`..`user_003` |
| `--semilla N` | `42` | semilla aleatoria |
| `--zona ZONA` | la del sistema | zona horaria de la curva diaria, por ejemplo `Europe/Madrid` |
| `--curva diaria\|plana` | `diaria` | tasa de sesiones según la hora local, o constante |
| `--velocidad X` | `1` | divide las esperas entre peticiones. Solo para pruebas rápidas: distorsiona los tiempos de espera que aprende MORFEO |
| `--bots X` | `0.02` | fracción de sesiones de bots |

Para una curva horaria con sentido hay que cubrir el día entero
(`--duracion 24h` o más): los timestamps del log son los reales de la API, así
que no se puede comprimir un día en una hora.

Prueba rápida (3 minutos, sin curva):

```bash
java -jar target/simulador-usuarios.jar --duracion 3m --sesiones-hora 3000 --curva plana --velocidad 10
```

## Qué hace un usuario

- **Llegadas:** proceso de Poisson. La tasa es `--sesiones-hora` por el peso de
  la hora (`CurvaHoraria`): casi nada de madrugada, picos a las 10 h y a las
  18-19 h.
- **Quién:** popularidad de Zipf. Unos pocos clientes entran mucho y la mayoría
  poco. Un cliente que aún no existe en la API empieza su primera sesión
  dándose de alta (`POST /api/usuario/crear`).
- **Recorrido:** cadena de Markov entre consultar saldo, ingresar, retirar y
  salir (`SesionUsuario`). La mayoría de sesiones empiezan consultando el saldo.
- **Esperas:** lognormales, con una mediana de 6 s tras consultar el saldo y de
  10 s tras una operación. Como máximo, 5 min.
- **Importes:** las retiradas suelen ser una parte del saldo que el cliente ha
  visto. A veces piden más de la cuenta, y la API responde 400 por fondos
  insuficientes.
- **Errores realistas:** un 1 % de los envíos lleva un JSON roto (400) y un 2 %
  de las sesiones son bots que piden rutas inexistentes (404) sin cookie.
- **Sesiones:** cada sesión guarda su cookie `MORFEO_SID`, así que en el log
  todas sus peticiones comparten `ID_SESION`.

## Del log al modelo

```bash
cd morfeo-python
morfeo generate --jar ../backend-java/target/bank-api-stress.jar --logs ../backend-java/trafico.json \
    --out-dir build --tz Europe/Madrid
```

## Tests

```bash
mvn test
```

`SesionUsuarioTest` levanta un servidor HTTP falso que imita a la API y
comprueba que las cookies se mantienen dentro de la sesión, que los clientes
nuevos se dan de alta una sola vez, que las peticiones y los cuerpos son los de
la API, y que los bots no llevan cookie.
