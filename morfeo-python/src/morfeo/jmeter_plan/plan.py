"""Conversor de un perfil de MORFEO a un plan de JMeter (.jmx).

Lo usan ``morfeo jmeter`` y ``morfeo generate``. Lee ``profile.json`` y
``endpoints.json`` (formato en ``morfeo-python/docs/contracts.md``) y, si hay,
``requests.csv`` para los usuarios y escribe un plan de JMeter 5.6.3 que
reproduce el comportamiento aprendido contra la API:

- Cada iteración de un hilo es una sesión de usuario, con su propia cookie
  ``MORFEO_SID`` (el *HTTP Cookie Manager* se vacía en cada iteración).
- El orden de las peticiones sigue la cadena de Markov del perfil, sorteada
  en tiempo de ejecución con Groovy (JSR223, incluido en JMeter).
- La espera entre peticiones se sortea de los 101 percentiles de
  ``think_time_ms``, según el estado anterior.
- La carga sigue la curva por hora del perfil (``--mode curve``) o una
  escalera de múltiplos de la hora pico (``--mode steps``). Las sesiones de
  cada grupo de hilos llegan como un único proceso de Poisson, que comparten
  todos sus hilos.
- El pool de usuarios se da de alta en un *setUp Thread Group* (se puede
  saltar con ``-Jcreate_pool=false``) o en un plan aparte (``--pool-only``),
  para que esas altas no entren en el ``trafico.json`` que se valida.

No depende del resto de MORFEO: solo lee ficheros y usa la biblioteca
estándar de Python 3.11. Con las mismas entradas y opciones, el plan sale
idéntico byte a byte.

Los parámetros del plan (modo, carga, hilos, usuarios, importes...) salen de
``Options``. Hoy los fijan los flags y las estadísticas del perfil; es el único
sitio que habría que alimentar para que otro componente (una IA, por ejemplo)
decida esos parámetros.

Uso::

    morfeo jmeter build/profile.json build/endpoints.json --requests build/requests.csv -o plan.jmx
    jmeter -n -t plan.jmx -l results.jtl -Jhost=localhost -Jport=7070
"""

from __future__ import annotations

import csv
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import quote

DEFAULT_SEED = 42
START_STATE = "START"
END_STATE = "END"
JMETER_VERSION = "5.6.3"

CREATE_WORDS = ("crear", "create", "register", "signup")
# Importes por defecto cuando el esquema no da límites: el saldo inicial de un
# alta es mayor que un movimiento (mismas medianas que simulador-usuarios).
INITIAL_NUMBER = ("lognormal", 300.0, 1.0)
MOVEMENT_NUMBER = ("lognormal", 80.0, 0.8)
INITIAL_WORDS = ("inicial", "initial", "balance", "saldo", "opening")
SPEND_WORDS = ("retir", "withdraw", "debit", "charge", "cargo")
DEFAULT_SPEND_KNOWN = 0.85  # sin dato en el perfil: 85 % de las retiradas caben en el saldo
DEFAULT_SPEND_FRACTION = (0.05, 0.55)  # parte del saldo visto que se retira
MAX_POOL_IDS = 1000
SETUP_THREADS = 4
MIN_MAX_STEPS = 50
MIN_TAIL_S = 30
# Prefijo de los ids del pool. Es fijo, no cambia en cada ejecución, para que
# un pool dado de alta antes (--pool-only) sirva a las ejecuciones siguientes.
DEFAULT_POOL_PREFIX = "mjpool"
# Latencia extra por petición con la que se dimensionan los hilos de la
# escalera: el tiempo que espera el backend por una conexión libre de la base
# de datos antes de responder 503 (DB_TIMEOUT_MS, 5000 ms por defecto).
DEFAULT_LATENCY_MARGIN_STEPS_S = 5.0

# Nombres de las variables de JMeter que usan los scripts.
VAR_MODEL = "morfeo_modelo"
VAR_PREFIX = "morfeo_prefijo"
VAR_POOL_PREFIX = "morfeo_prefijo_pool"
VAR_SEED = "morfeo_semilla"
VAR_USER = "morfeo_usuario"
VAR_STATE = "morfeo_estado"
VAR_CONTINUE = "morfeo_seguir"

# {x}, {x:regex} (con un nivel de llaves dentro), {*x}, ** y *: las mismas
# formas de variable que acepta el jar_scanner en ``path``.
_PATH_VAR_RE = re.compile(r"\{(?:[^{}]|\{[^{}]*\})*\}|\*\*|\*")
_INVALID_XML_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


# --------------------------------------------------------------------------
# Scripts de Groovy
#
# Ninguno contiene la secuencia dólar-llave: JMeter la interpretaría como una
# variable o función suya antes de pasar el script a Groovy. Por eso tampoco
# se usan GString con llaves.
# --------------------------------------------------------------------------

GROOVY_LOADER = """\
// Modelo de MORFEO: el JSON está en la variable morfeo_modelo del plan. Se
// interpreta una vez por ejecución y se comparte entre hilos en props.
String fuente = vars.get('morfeo_modelo')
Map cache = (Map) props.get('morfeo.modelo')
if (cache == null || !fuente.equals(cache.fuente)) {
    synchronized (props) {
        cache = (Map) props.get('morfeo.modelo')
        if (cache == null || !fuente.equals(cache.fuente)) {
            def j = new groovy.json.JsonSlurper().parseText(fuente)
            def fila = { f -> [f.destinos.collect { it as int } as int[], f.acumulada.collect { it as double } as double[]] }
            def tabla = { l -> l == null ? null : (l.collect { it as double } as double[]) }
            Map m = [:]
            m.estados = j.estados
            m.fin = j.fin as int
            m.inicio = fila(j.inicio)
            m.filas = j.filas.collect { fila(it) }
            m.esperaGlobal = tabla(j.espera_global)
            m.espera = j.espera_por_estado.collect { tabla(it) }
            m.maxPasos = j.max_pasos as int
            m.pool = j.pool as int
            m.formatoPool = '%0' + j.pool_ancho + 'd'
            m.usuarios = j.usuarios == null ? null : j.usuarios.collect { it as String }
            m.usuariosAcum = j.usuarios_acum == null ? null : (j.usuarios_acum.collect { it as double } as double[])
            m.gastoMin = j.gasto_fraccion[0] as double
            m.gastoMax = j.gasto_fraccion[1] as double
            Map numeros = [:]
            j.numeros.each { k, l ->
                numeros[k] = l.collect { f -> [f[0] as String, f[1] == 'uniform', f[2] as double, f[3] as double, f[4] as boolean, f[5] as double, f[6] as double] }
            }
            m.numeros = numeros
            Map textos = [:]
            j.textos.each { k, l ->
                textos[k] = l.collect { f -> [f[0] as String, f[1] as String, f[2], f[3]] }
            }
            m.textos = textos
            cache = [fuente: fuente, m: m]
            props.put('morfeo.modelo', cache)
            log.info('MORFEO: modelo cargado con ' + m.estados.size() + ' estados')
        }
    }
}
Map M = (Map) cache.m

// Generador aleatorio propio de cada hilo: semilla, grupo (args[0]), número de
// hilo y, en modo distribuido, el servidor. Un servidor de JMeter (-R) guarda
// su host:puerto en la propiedad __jm.D_TG antes de cada prueba; en una
// ejecución local está vacía. Con ella cada servidor sortea sesiones distintas
// y añade su nombre al prefijo de las altas en sesión, para que no choquen.
Random rng = (Random) vars.getObject('morfeo_rng')
if (rng == null) {
    String servidor = props.getProperty('__jm.D_TG', '').trim()
    long base = Long.parseLong(vars.get('morfeo_semilla').trim()) * 1000003L + Long.parseLong(args[0]) * 10007L + ctx.getThreadNum()
    if (!servidor.isEmpty()) {
        base = base * 1000033L + servidor.hashCode()
        vars.put('morfeo_prefijo', vars.get('morfeo_prefijo') + '_' + servidor.replaceAll('[^A-Za-z0-9]', '-'))
    }
    rng = new Random(new SplittableRandom(base).nextLong())
    vars.putObject('morfeo_rng', rng)
    vars.put('morfeo_grupo', args[0])
    vars.put('morfeo_servidor', servidor)
}
"""

GROOVY_POOL_ID = """
// Id del usuario número n (desde 1) del pool: de la lista del modelo o, sin
// lista, <prefijo>_p<n>.
String idPool(Map M, def vars, int n) {
    if (M.usuarios != null) {
        return ((List) M.usuarios).get(n - 1)
    }
    return vars.get('morfeo_prefijo_pool') + '_p' + String.format((String) M.formatoPool, n)
}
"""

GROOVY_SAMPLE_ROW = """
// Siguiente estado de una fila de la matriz: destinos y probabilidad acumulada.
int sortear(List fila, Random rng) {
    int[] destinos = (int[]) fila[0]
    double[] acumulada = (double[]) fila[1]
    double u = rng.nextDouble()
    for (int i = 0; i < acumulada.length; i++) {
        if (u < acumulada[i]) {
            return destinos[i]
        }
    }
    return destinos[destinos.length - 1]
}
"""

GROOVY_SESSION_START = (
    "// Inicio de sesión: usuario al azar del pool y primer estado (fila START).\n"
    "// Parámetros: índice del grupo de hilos.\n"
    + GROOVY_LOADER
    + """
// Con lista de usuarios (sacada de los logs) se elige con la frecuencia con que
// cada uno aparece en ellos; sin lista, al azar entre los del pool.
int n
if (M.usuariosAcum != null) {
    double[] acum = (double[]) M.usuariosAcum
    double u = rng.nextDouble()
    n = 1
    while (n < acum.length && u >= acum[n - 1]) {
        n++
    }
} else {
    n = 1 + rng.nextInt(M.pool as int)
}
vars.put('morfeo_usuario', idPool(M, vars, n))
vars.put('morfeo_estado', String.valueOf(sortear((List) M.inicio, rng)))
vars.put('morfeo_anterior', '-1')
vars.put('morfeo_paso', '0')
vars.put('morfeo_seguir', 'true')
vars.put('morfeo_saldo', '')
"""
    + GROOVY_POOL_ID
    + GROOVY_SAMPLE_ROW
)

GROOVY_ARRIVALS = """\
// Llegadas de sesiones: un solo proceso de Poisson por grupo de hilos, de tasa
// args[0] sesiones por segundo, compartido por todos sus hilos. Cada hilo que
// queda libre toma la siguiente llegada prevista del grupo y espera hasta su
// hora. Si esa hora ya ha pasado (todos los hilos estaban ocupados), la sesión
// empieza en el acto y se anota el retraso. La ventana (args[1] segundos)
// empieza con el primer hilo del grupo. Ninguna sesión empieza después de la
// ventana: las llegadas que no han podido empezar dentro de ella se descartan
// y se cuentan. El estado del grupo está en props, con la hora de inicio de la
// prueba en la clave, y el resumen lo escribe el tearDown.
double tasa = Double.parseDouble(args[0])
long ventanaMs = Math.round(Double.parseDouble(args[1]) * 1000d)
String clave = 'morfeo.llegadas.' + org.apache.jmeter.threads.JMeterContextService.getTestStartTime() + '.' + vars.get('morfeo_grupo')
Map L = (Map) props.get(clave)
if (L == null) {
    synchronized (props) {
        L = (Map) props.get(clave)
        if (L == null) {
            long base = (Long.parseLong(vars.get('morfeo_semilla').trim()) * 1000003L + Long.parseLong(vars.get('morfeo_grupo')) * 10007L) ^ 0x5DEECE66DL
            String servidor = vars.get('morfeo_servidor') ?: ''
            if (!servidor.isEmpty()) {
                base = base * 1000033L + servidor.hashCode()
            }
            long t0 = System.currentTimeMillis()
            L = new HashMap()
            L.grupo = ctx.getThreadGroup().getName()
            L.indice = Integer.parseInt(vars.get('morfeo_grupo'))
            L.tasa = tasa
            L.ventana = ventanaMs
            L.fin = t0 + ventanaMs
            L.siguiente = (double) t0
            L.rng = new Random(new SplittableRandom(base).nextLong())
            L.cerrada = false
            L.empezadas = 0L
            L.tarde = 0L
            L.retraso = 0d
            L.retrasoMax = 0d
            L.descartadas = 0L
            props.put(clave, L)
        }
    }
}
long ahora = System.currentTimeMillis()
double llegada = -1d
synchronized (L) {
    Random r = (Random) L.rng
    long fin = (long) L.fin
    if (!L.cerrada && ahora >= fin) {
        // La ventana ha acabado con llegadas sin empezar: se cuentan y se cierra.
        double x = (double) L.siguiente + (-Math.log(1d - r.nextDouble()) / tasa) * 1000d
        while (x < fin) {
            L.siguiente = x
            L.descartadas = (long) L.descartadas + 1L
            x = x + (-Math.log(1d - r.nextDouble()) / tasa) * 1000d
        }
        L.cerrada = true
    }
    if (!L.cerrada) {
        double x = (double) L.siguiente + (-Math.log(1d - r.nextDouble()) / tasa) * 1000d
        if (x >= fin) {
            L.cerrada = true
        } else {
            L.siguiente = x
            llegada = x
            double retraso = Math.max(0d, ahora - x)
            L.empezadas = (long) L.empezadas + 1L
            L.retraso = (double) L.retraso + retraso
            L.retrasoMax = Math.max((double) L.retrasoMax, retraso)
            if (retraso > 1000d) {
                L.tarde = (long) L.tarde + 1L
            }
        }
    }
}
if (llegada < 0d) {
    vars.put('morfeo_seguir', 'false')
    ctx.getThread().stop()
    return 0L
}
return Math.max(0L, Math.round(llegada - ahora))
"""

GROOVY_SUMMARY = """\
// Resumen de las llegadas de cada grupo de hilos, en jmeter.log y en la
// consola: llegadas sorteadas en la ventana, sesiones empezadas, retrasos y
// llegadas que no pudieron empezar porque todos los hilos estaban ocupados.
String pre = 'morfeo.llegadas.' + org.apache.jmeter.threads.JMeterContextService.getTestStartTime() + '.'
List claves = new ArrayList(props.keySet()).findAll { it.toString().startsWith(pre) }
claves.sort { a, b -> (((Map) props.get(a)).indice as int) <=> (((Map) props.get(b)).indice as int) }
for (Object k : claves) {
    Map L = (Map) props.get(k)
    String linea
    synchronized (L) {
        if (!L.cerrada) {
            // El grupo acabó con todos los hilos ocupados: faltan llegadas por contar.
            Random r = (Random) L.rng
            double tasa = (double) L.tasa
            long fin = (long) L.fin
            double x = (double) L.siguiente + (-Math.log(1d - r.nextDouble()) / tasa) * 1000d
            while (x < fin) {
                L.descartadas = (long) L.descartadas + 1L
                x = x + (-Math.log(1d - r.nextDouble()) / tasa) * 1000d
            }
            L.cerrada = true
        }
        long empezadas = (long) L.empezadas
        long descartadas = (long) L.descartadas
        double ventanaS = ((long) L.ventana) / 1000d
        double tasaH = ((double) L.tasa) * 3600d
        linea = String.format(Locale.ROOT,
            'MORFEO: %s: %d llegadas en la ventana de %.0f s (media prevista %.1f); %d sesiones empezadas, %.1f por hora (pedidas %.1f); %d con más de 1 s de retraso, retraso medio %.2f s, máximo %.1f s; %d sin empezar por falta de hilos libres',
            L.grupo, empezadas + descartadas, ventanaS, tasaH * ventanaS / 3600d, empezadas, empezadas * 3600d / ventanaS, tasaH,
            (long) L.tarde, empezadas > 0 ? ((double) L.retraso) / empezadas / 1000d : 0d, ((double) L.retrasoMax) / 1000d, descartadas)
    }
    log.info(linea)
    OUT.println(linea)
    props.remove(k)
}
"""

GROOVY_THINK_TIME = """\
// Espera desde el fin de la petición anterior hasta el inicio de esta:
// think_time_ms.by_state del estado anterior o, si no hay, el global. Se
// sortea por inversión de los 101 percentiles con interpolación lineal, como
// sample_from_dist de MORFEO. La primera petición de la sesión no espera.
if (vars.get('morfeo_paso') == '0') {
    return 0L
}
Map M = (Map) props.get('morfeo.modelo').m
Random rng = (Random) vars.getObject('morfeo_rng')
int anterior = Integer.parseInt(vars.get('morfeo_anterior'))
double[] q = (double[]) M.espera[anterior]
if (q == null) {
    q = (double[]) M.esperaGlobal
}
if (q == null || q.length == 0) {
    return 0L
}
double x = rng.nextDouble() * (q.length - 1)
int i = (int) x
double v = (i >= q.length - 1) ? q[q.length - 1] : q[i] + (q[i + 1] - q[i]) * (x - i)
return Math.max(0L, Math.round(v))
"""

GROOVY_NEXT_STATE = (
    """\
// Siguiente estado de la sesión, sorteado en la fila del estado que se acaba
// de ejecutar. La sesión termina en END o al llegar al máximo de pasos.
Map M = (Map) props.get('morfeo.modelo').m
Random rng = (Random) vars.getObject('morfeo_rng')
// El saldo que ve el usuario en la respuesta ("saldo" o "saldo_actual"): sirve
// para que una retirada sea, casi siempre, una parte de lo que tiene.
String cuerpo = prev == null ? null : prev.getResponseDataAsString()
if (cuerpo != null) {
    java.util.regex.Matcher visto = java.util.regex.Pattern.compile('"saldo(_actual)?"[ ]*:[ ]*(-?[0-9][0-9.Ee+-]*)').matcher(cuerpo)
    if (visto.find()) {
        vars.put('morfeo_saldo', visto.group(2))
    }
}
int actual = Integer.parseInt(vars.get('morfeo_estado'))
int paso = Integer.parseInt(vars.get('morfeo_paso')) + 1
vars.put('morfeo_anterior', String.valueOf(actual))
vars.put('morfeo_paso', String.valueOf(paso))
int siguiente = sortear((List) M.filas[actual], rng)
if (siguiente == (M.fin as int) || paso >= (M.maxPasos as int)) {
    vars.put('morfeo_seguir', 'false')
} else {
    vars.put('morfeo_estado', String.valueOf(siguiente))
}
"""
    + GROOVY_SAMPLE_ROW
)

GROOVY_NEW_USER = """\
// La sesión da de alta un usuario: pasa a un id nuevo y único, que usa
// hasta el final de la sesión: <prefijo>_t<grupo>-<hilo>_<contador>.
int c = Integer.parseInt(vars.get('morfeo_altas') ?: '0') + 1
vars.put('morfeo_altas', String.valueOf(c))
vars.put('morfeo_usuario', vars.get('morfeo_prefijo') + '_t' + vars.get('morfeo_grupo') + '-' + (ctx.getThreadNum() + 1) + '_' + c)
"""

GROOVY_NUMBERS = """\
// Valores del cuerpo de esta petición (args[0] es su clave en el modelo).
// Números: lognormal (mediana por exp(sigma por N(0,1))) o uniforme, nunca por
// debajo del mínimo del campo. Si el campo es un importe a retirar y se conoce
// el saldo, con la probabilidad de f[6] es una parte del saldo visto
// (si no, puede no haber fondos). Enteros redondeados; el resto, con dos
// decimales. Textos: elegidos entre los valores del enum, uuid, email, fecha o
// texto de la longitud del esquema.
Map M = (Map) props.get('morfeo.modelo').m
Random rng = (Random) vars.getObject('morfeo_rng')
double saldo = -1d
String visto = vars.get('morfeo_saldo')
if (visto != null && !visto.isEmpty()) {
    try { saldo = Double.parseDouble(visto) } catch (NumberFormatException e) { saldo = -1d }
}
for (List f : (List) M.numeros[args[0]]) {
    double a = (double) f[2]
    double b = (double) f[3]
    double v
    if (f[1]) {
        v = a + (b - a) * rng.nextDouble()
    } else {
        v = a * Math.exp(b * rng.nextGaussian())
    }
    v = Math.max((double) f[5], v)
    double p = (double) f[6]
    if (p >= 0d && saldo > 0d && rng.nextDouble() < p) {
        v = Math.floor(saldo * ((double) M.gastoMin + ((double) M.gastoMax - (double) M.gastoMin) * rng.nextDouble()) * 100d) / 100d
        v = Math.max(0.01d, v)
    }
    String texto = f[4] ? String.valueOf(Math.round(v)) : BigDecimal.valueOf(v).setScale(2, java.math.RoundingMode.HALF_UP).toPlainString()
    vars.put((String) f[0], texto)
}
for (List f : (List) M.textos[args[0]]) {
    String tipo = (String) f[1]
    String texto
    if (tipo == 'enum') {
        List valores = (List) f[2]
        texto = String.valueOf(valores.get(rng.nextInt(valores.size())))
    } else if (tipo == 'uuid') {
        texto = new UUID(rng.nextLong(), rng.nextLong()).toString()
    } else if (tipo == 'email') {
        texto = 'usuario' + rng.nextInt(1000000) + '@example.com'
    } else if (tipo == 'date') {
        texto = java.time.LocalDate.now().minusDays(rng.nextInt(365)).toString()
    } else if (tipo == 'date-time') {
        texto = java.time.Instant.now().minusSeconds(rng.nextInt(86400 * 30)).toString()
    } else {
        int minimo = ((List) f[2])[0] as int
        int maximo = ((List) f[2])[1] as int
        int largo = minimo + rng.nextInt(maximo - minimo + 1)
        StringBuilder sb = new StringBuilder()
        String letras = 'abcdefghijklmnopqrstuvwxyz'
        for (int i = 0; i < largo; i++) {
            sb.append(letras.charAt(rng.nextInt(letras.length())))
        }
        texto = sb.toString()
    }
    vars.put((String) f[0], texto)
}
"""

GROOVY_SETUP_START = (
    "// Alta del pool de usuarios: cada hilo da de alta los ids n, n + hilos, ...\n"
    "// Parámetros: índice del grupo (0) y número de hilos del setUp. Con la\n"
    "// propiedad create_pool=false no se da de alta nadie (el pool ya existe).\n"
    + GROOVY_LOADER
    + """
if (props.getProperty('create_pool', 'true').trim().equalsIgnoreCase('false')) {
    vars.put('morfeo_seguir', 'false')
    if (ctx.getThreadNum() == 0) {
        log.info('MORFEO: create_pool=false, no se da de alta el pool')
    }
    return
}
int n = ctx.getThreadNum() + 1
vars.put('morfeo_alta_n', String.valueOf(n))
vars.put('morfeo_seguir', n <= (M.pool as int) ? 'true' : 'false')
if (n <= (M.pool as int)) {
    vars.put('morfeo_usuario', idPool(M, vars, n))
}
"""
    + GROOVY_POOL_ID
)

GROOVY_SETUP_NEXT = """\
// Siguiente id del pool para este hilo. Parámetro: número de hilos del setUp.
Map M = (Map) props.get('morfeo.modelo').m
int n = Integer.parseInt(vars.get('morfeo_alta_n')) + Integer.parseInt(args[0])
vars.put('morfeo_alta_n', String.valueOf(n))
if (n > (M.pool as int)) {
    vars.put('morfeo_seguir', 'false')
} else {
    vars.put('morfeo_usuario', idPool(M, vars, n))
}
""" + GROOVY_POOL_ID

ALL_SCRIPTS = (
    GROOVY_SESSION_START,
    GROOVY_ARRIVALS,
    GROOVY_SUMMARY,
    GROOVY_THINK_TIME,
    GROOVY_NEXT_STATE,
    GROOVY_NEW_USER,
    GROOVY_NUMBERS,
    GROOVY_SETUP_START,
    GROOVY_SETUP_NEXT,
)


# --------------------------------------------------------------------------
# Opciones
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberDist:
    """Distribución de un campo numérico del cuerpo."""

    kind: str  # "lognormal" (a = mediana, b = sigma) o "uniform" (a = mínimo, b = máximo)
    a: float
    b: float


def parse_number_spec(text: str) -> tuple[str, NumberDist]:
    """``campo=lognormal:MEDIANA:SIGMA`` o ``campo=uniform:MIN:MAX``. ``*`` cambia el valor por defecto."""
    name, sep, spec = text.partition("=")
    name = name.strip()
    parts = spec.split(":")
    if not sep or not name or len(parts) != 3:
        raise ValueError(f"--number '{text}': se espera campo=lognormal:MEDIANA:SIGMA o campo=uniform:MIN:MAX")
    kind = parts[0].strip().lower()
    try:
        a, b = float(parts[1]), float(parts[2])
    except ValueError:
        raise ValueError(f"--number '{text}': los parámetros tienen que ser números") from None
    if not (math.isfinite(a) and math.isfinite(b)):
        raise ValueError(f"--number '{text}': los parámetros tienen que ser finitos")
    if kind == "lognormal":
        if a <= 0 or b < 0:
            raise ValueError(f"--number '{text}': la mediana tiene que ser > 0 y sigma >= 0")
    elif kind == "uniform":
        if b < a:
            raise ValueError(f"--number '{text}': el máximo es menor que el mínimo")
    else:
        raise ValueError(f"--number '{text}': distribución '{kind}' desconocida (lognormal o uniform)")
    return name, NumberDist(kind, a, b)


def parse_hours(text: str) -> list[int]:
    """``8-20``, ``8,9,10`` o ``22-2`` (cruza la medianoche). Sin repetidos, en el orden escrito."""
    out: list[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        lo_text, sep, hi_text = token.partition("-")
        try:
            lo = int(lo_text)
            hi = int(hi_text) if sep else lo
        except ValueError:
            raise ValueError(f"--hours '{text}': '{token}' no es una hora ni un rango") from None
        if not (0 <= lo <= 23 and 0 <= hi <= 23):
            raise ValueError(f"--hours '{text}': las horas van de 0 a 23")
        span = list(range(lo, hi + 1)) if lo <= hi else [*range(lo, 24), *range(0, hi + 1)]
        out.extend(h for h in span if h not in out)
    if not out:
        raise ValueError(f"--hours '{text}': no hay ninguna hora")
    return out


def parse_steps(text: str) -> list[float]:
    """Multiplicadores de la escalera: ``1,2,5,10``."""
    try:
        steps = [float(t) for t in text.split(",") if t.strip()]
    except ValueError:
        raise ValueError(f"--steps '{text}': se espera una lista de números separados por comas") from None
    if not steps or any(not math.isfinite(s) or s <= 0 for s in steps):
        raise ValueError(f"--steps '{text}': los multiplicadores tienen que ser positivos")
    return steps


# --------------------------------------------------------------------------
# Estados del perfil y endpoints del jar
# --------------------------------------------------------------------------


def clean_path(path: str) -> str:
    """Ruta para comparar: sin ``//`` repetidas ni barra final (salvo ``/``)."""
    p = re.sub(r"/{2,}", "/", path or "/")
    if len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/") or "/"
    return p or "/"


def state_method_path(profile: dict[str, Any], state: str) -> tuple[str, str]:
    """Método y plantilla de un estado, de ``profile.endpoints`` o de su nombre."""
    info = _dig(profile, "endpoints", state) or {}
    method = str(info.get("method") or "").upper()
    path = str(info.get("path") or "")
    if not method or not path:
        method, _, path = state.partition(" ")
    return method.upper(), clean_path(path)


def match_states(
    profile: dict[str, Any], endpoints_doc: dict[str, Any], replay_unmatched: bool = True
) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    """Estados del perfil que tienen endpoint en endpoints.json.

    Se comparan la ruta y el método; un endpoint ``ANY`` acepta cualquier
    método. Devuelve los estados que se conservan (en el orden del perfil),
    el endpoint de cada uno y los que se descartan.

    Un estado que el perfil marca como ``matched: false`` (una ruta de los logs
    que no está en el jar, por ejemplo un bot que pide ``/wp-admin``) es tráfico
    real que el servidor recibe: con ``replay_unmatched`` se reproduce como un
    GET/POST sin parámetros ni cuerpo, con un endpoint ``_ghost``.
    """
    endpoints = endpoints_doc.get("endpoints") or []
    kept: list[str] = []
    found: dict[str, dict[str, Any]] = {}
    dropped: list[str] = []
    for state in profile.get("states") or []:
        if state in (START_STATE, END_STATE):
            continue
        method, path = state_method_path(profile, state)
        exact = [ep for ep in endpoints if clean_path(str(ep.get("path", ""))) == path and str(ep.get("method", "")).upper() == method]
        anys = [ep for ep in endpoints if clean_path(str(ep.get("path", ""))) == path and str(ep.get("method", "")).upper() == "ANY"]
        ep = (exact or anys or [None])[0]
        info = _dig(profile, "endpoints", state) or {}
        if ep is None and replay_unmatched and info.get("matched") is False:
            ep = {"id": state, "method": method, "path": path, "params": [], "body": None, "_ghost": True}
        if ep is None:
            dropped.append(state)
        else:
            kept.append(state)
            found[state] = ep
    return kept, found, dropped


@dataclass
class Chain:
    """Cadena de Markov reducida a los estados que tienen sampler."""

    states: list[str]
    start: dict[str, float]
    rows: dict[str, dict[str, float]]
    warnings: list[str] = field(default_factory=list)


def _renormalise(row: dict[str, Any], allowed: set[str]) -> tuple[dict[str, float], float]:
    kept = {b: float(p) for b, p in row.items() if b in allowed and p is not None and float(p) > 0}
    dropped = sum(float(p) for b, p in row.items() if b not in allowed and p is not None and float(p) > 0)
    total = sum(kept.values())
    if total <= 0:
        return {}, dropped
    return {b: p / total for b, p in kept.items()}, dropped


def build_chain(profile: dict[str, Any], states: Sequence[str]) -> Chain:
    """Matriz de transición sin los estados descartados, con cada fila renormalizada."""
    matrix = profile.get("transition_matrix") or {}
    allowed = set(states)
    warnings: list[str] = []
    start, lost = _renormalise(matrix.get(START_STATE) or {}, allowed)
    if not start:
        raise ValueError("la fila START del perfil no lleva a ningún estado con endpoint en endpoints.json")
    if lost > 1e-12:
        warnings.append(f"START: se descarta un {lost:.1%} de transiciones a estados sin endpoint; la fila se renormaliza")
    rows: dict[str, dict[str, float]] = {}
    for state in states:
        row, lost = _renormalise(matrix.get(state) or {}, allowed | {END_STATE})
        if not row:
            warnings.append(f"{state}: la fila no lleva a ningún estado conocido; se trata como fin de sesión")
            row = {END_STATE: 1.0}
        elif lost > 1e-12:
            warnings.append(f"{state}: se descarta un {lost:.1%} de transiciones a estados sin endpoint; la fila se renormaliza")
        rows[state] = row
    return Chain(list(states), start, rows, warnings)


def cumulative(row: dict[str, float], index: dict[str, int]) -> dict[str, list[Any]]:
    """Fila como destinos (índices; END es el último) y probabilidad acumulada, la última a 1."""
    items = sorted(((index[b], p) for b, p in row.items() if p > 0), key=lambda t: t[0])
    dest: list[int] = []
    acc: list[float] = []
    total = 0.0
    for i, p in items:
        total += p
        dest.append(i)
        acc.append(total)
    if acc:
        acc[-1] = 1.0  # el sorteo nunca cae fuera de la fila por redondeo
    return {"destinos": dest, "acumulada": acc}


def has_id_field(endpoint: dict[str, Any], id_field: str) -> bool:
    body = endpoint.get("body") or {}
    example = body.get("example")
    props = _dig(body, "schema", "properties") or {}
    return (isinstance(example, dict) and id_field in example) or id_field in props


def detect_create_endpoint(endpoints_doc: dict[str, Any], id_field: str) -> list[dict[str, Any]]:
    """Candidatos a endpoint de alta: POST con crear/create/register/signup en la ruta y el campo id en el cuerpo."""
    out = []
    for ep in endpoints_doc.get("endpoints") or []:
        if str(ep.get("method", "")).upper() != "POST":
            continue
        path = str(ep.get("path", "")).lower()
        if any(w in path for w in CREATE_WORDS) and has_id_field(ep, id_field):
            out.append(ep)
    return out


# --------------------------------------------------------------------------
# Peticiones
# --------------------------------------------------------------------------


def jmeter_literal(text: str) -> str:
    """Texto que JMeter debe enviar tal cual aunque contenga dólar-llave."""
    if "${" not in text:
        return text
    return text.replace("\\", "\\\\").replace("$", "\\$")


def var_ref(name: str) -> str:
    return "${" + name + "}"


def _param_value(param: dict[str, Any]) -> str:
    """Valor de un parámetro: ejemplo, valor por defecto, primer enum o ``1``."""
    for key in ("example", "default"):
        value = param.get(key)
        if value is not None and value != "":
            return _scalar_text(value)
    schema = param.get("schema") or {}
    if schema.get("example") is not None:
        return _scalar_text(schema["example"])
    if schema.get("enum"):
        return str(schema["enum"][0])
    if schema.get("type") == "boolean":
        return "true"
    return "1"


def _optional_without_value(param: dict[str, Any]) -> bool:
    """Parámetro opcional sin ejemplo ni valor por defecto: no se envía."""
    return not param.get("required") and param.get("default") is None and param.get("example") is None


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _var_name(path: Sequence[str], used: set[str]) -> str:
    base = "morfeo_num_" + ("_".join(re.sub(r"[^A-Za-z0-9]", "_", p) for p in path) or "valor")
    name, n = base, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


@dataclass
class NumberField:
    """Campo numérico del cuerpo que se sortea en cada petición."""

    var: str
    name: str
    dist: NumberDist
    integer: bool
    floor: float = 1.0  # mínimo del valor sorteado
    spend: float = -1.0  # >= 0: importe a retirar; probabilidad de que sea una parte del saldo visto

    def as_model(self) -> list[Any]:
        return [self.var, self.dist.kind, self.dist.a, self.dist.b, self.integer, self.floor, self.spend]


@dataclass
class TextField:
    """Campo de texto del cuerpo que se sortea según su esquema (enum, uuid, email, fecha, longitud)."""

    var: str
    name: str
    kind: str  # enum, uuid, email, date, date-time, text
    arg: Any = None  # valores del enum o [mínimo, máximo] de longitud

    def as_model(self) -> list[Any]:
        return [self.var, self.kind, self.arg, None]


def _constraints(schema: Any) -> dict[str, Any]:
    return ((schema or {}).get("constraints") or {}) if isinstance(schema, dict) else {}


def number_dist_for(name: str, schema: Any, integer: bool, numbers: dict[str, NumberDist]) -> tuple[NumberDist, float]:
    """Distribución y mínimo de un campo numérico.

    Manda ``--number``. Si no, el esquema: con mínimo y máximo, uniforme entre
    ellos. Si no, lognormal con la mediana de un saldo inicial o de un
    movimiento según el nombre del campo, con el mínimo del esquema (1 para
    enteros y 0,01 para decimales positivos).
    """
    c = _constraints(schema)
    lo, hi = _num(c.get("min")), _num(c.get("max"))
    given = numbers.get(name) or numbers.get("*")
    if lo is not None:
        floor = lo
    elif c.get("positive") and not integer:
        floor = 0.01
    else:
        floor = 1.0
    if given is not None:
        return given, floor
    if lo is not None and hi is not None and hi > lo:
        return NumberDist("uniform", lo, hi), lo
    kind, a, b = INITIAL_NUMBER if any(w in name.lower() for w in INITIAL_WORDS) else MOVEMENT_NUMBER
    return NumberDist(kind, a, b), floor


def text_kind_for(schema: Any) -> tuple[str, Any] | None:
    """Cómo sortear un campo de texto a partir de su esquema, o ``None`` si se deja el ejemplo."""
    if not isinstance(schema, dict):
        return None
    c = _constraints(schema)
    fmt = str(schema.get("format") or "")
    if schema.get("enum"):
        return "enum", [str(v) for v in schema["enum"]]
    if fmt == "uuid":
        return "uuid", None
    if c.get("email") or fmt == "email":
        return "email", None
    if fmt in ("date", "date-time"):
        return fmt, None
    lo, hi = _num(c.get("min_length")), _num(c.get("max_length"))
    if lo is not None or hi is not None:
        mn = max(1, int(lo)) if lo is not None else 1
        mx = min(64, max(mn, int(hi))) if hi is not None else max(mn, 12)
        return "text", [mn, max(mn, mx)]
    return None


def body_template(
    example: Any,
    id_field: str,
    numbers: dict[str, NumberDist],
    schema: Any = None,
    spend: float = -1.0,
) -> tuple[str, list[NumberField], bool, list[TextField]]:
    """Cuerpo JSON del sampler a partir de ``body.example`` y su esquema.

    El campo id del primer nivel pasa a la variable del usuario de la sesión;
    cada campo numérico, a una variable que se sortea antes de enviar (con los
    límites del esquema y, en una retirada, una parte del saldo visto), y los
    textos con enum, formato o longitud, también. Los demás valores se copian
    del ejemplo. Devuelve el texto, los campos numéricos, si lleva el campo id
    y los campos de texto.
    """
    fields: list[NumberField] = []
    texts: list[TextField] = []
    used: set[str] = set()
    user_token = "@@MORFEO_USUARIO@@"
    has_user = False

    def walk(value: Any, path: list[str], top: bool, sch: Any) -> Any:
        nonlocal has_user
        props = (sch.get("properties") or {}) if isinstance(sch, dict) else {}
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if top and k == id_field and not isinstance(v, (dict, list)):
                    out[k] = user_token
                    has_user = True
                else:
                    out[k] = walk(v, [*path, str(k)], False, props.get(k))
            return out
        if isinstance(value, list):
            items = sch.get("items") if isinstance(sch, dict) else None
            return [walk(v, [*path, str(i)], False, items) for i, v in enumerate(value)]
        if isinstance(value, str):
            kind = text_kind_for(sch)
            if kind is None:
                return value
            name = next((p for p in reversed(path) if not p.isdigit()), "valor")
            texts.append(TextField(_var_name(path, used).replace("morfeo_num_", "morfeo_txt_"), name, kind[0], kind[1]))
            return f"@@MORFEO_TXT_{len(texts) - 1}@@"
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            name = next((p for p in reversed(path) if not p.isdigit()), "valor")
            is_int = isinstance(value, int) or (isinstance(sch, dict) and sch.get("type") == "integer")
            dist, floor = number_dist_for(name, sch, is_int, numbers)
            fields.append(NumberField(_var_name(path, used), name, dist, is_int, floor, spend))
            return f"@@MORFEO_NUM_{len(fields) - 1}@@"
        return value

    text = json.dumps(walk(example, [], True, schema), ensure_ascii=False, separators=(",", ":"))
    # El dólar del ejemplo se escribe como escape JSON para que JMeter no lo lea como variable.
    text = text.replace("$", "\\u0024")
    if has_user or fields or texts:
        # JMeter procesa las barras invertidas de un texto con variables: se duplican.
        text = text.replace("\\", "\\\\")
    if has_user:
        text = text.replace(f'"{user_token}"', '"' + var_ref(VAR_USER) + '"')
    for i, f in enumerate(fields):
        text = text.replace(f'"@@MORFEO_NUM_{i}@@"', var_ref(f.var))
    for i, t in enumerate(texts):
        text = text.replace(f'"@@MORFEO_TXT_{i}@@"', '"' + var_ref(t.var) + '"')
    return text, fields, has_user, texts


@dataclass
class RequestSpec:
    """Todo lo que necesita un HTTP Request de JMeter."""

    name: str
    method: str
    path: str
    body: str | None = None
    content_type: str | None = None
    form: list[tuple[str, str]] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    numbers: list[NumberField] = field(default_factory=list)
    texts: list[TextField] = field(default_factory=list)
    uses_user: bool = False


def build_request(
    name: str,
    method: str,
    endpoint: dict[str, Any],
    id_field: str,
    numbers: dict[str, NumberDist],
    warnings: list[str],
    spend: float = -1.0,
) -> RequestSpec:
    """Petición de un estado a partir de su endpoint en endpoints.json."""
    params = endpoint.get("params") or []
    by_name = {str(p.get("name")): p for p in params if p.get("in") == "path"}
    template = str(endpoint.get("path") or "/")
    uses_user = False

    def path_value(m: re.Match[str]) -> str:
        nonlocal uses_user
        token = m.group(0)
        if token in ("*", "**"):
            return "1"
        var = token[1:-1].lstrip("*").split(":", 1)[0]
        if var == id_field:
            uses_user = True
            return var_ref(VAR_USER)
        param = by_name.get(var)
        return quote(_param_value(param) if param else "1", safe="")

    # Los trozos literales se codifican como en una URL; el dólar también, para
    # que JMeter no lo lea como variable. La variable del usuario va sin codificar.
    literal_safe = "/;:@!&'()*+,=-._~"
    pieces: list[str] = []
    pos = 0
    for m in _PATH_VAR_RE.finditer(template):
        pieces.append(quote(template[pos : m.start()], safe=literal_safe))
        pieces.append(path_value(m))
        pos = m.end()
    pieces.append(quote(template[pos:], safe=literal_safe))
    spec = RequestSpec(name=name, method=method, path="".join(pieces))

    query: list[str] = []
    for p in params:
        where, pname = p.get("in"), str(p.get("name"))
        is_user = pname == id_field
        value = var_ref(VAR_USER) if is_user else None
        if where in ("query", "header", "form") and not is_user and _optional_without_value(p):
            continue  # opcional sin valor conocido: no se envía
        if where == "query" or (where == "form" and method in ("GET", "HEAD", "DELETE", "OPTIONS")):
            text = value or quote(_param_value(p), safe="")
            query.append(f"{quote(pname, safe='')}={text}")
            uses_user = uses_user or is_user
        elif where == "header":
            spec.headers.append((pname, value or jmeter_literal(_param_value(p))))
            uses_user = uses_user or is_user
        elif where == "form":
            spec.form.append((pname, value or jmeter_literal(_param_value(p))))
            uses_user = uses_user or is_user
        elif where == "cookie":
            warnings.append(f"{name}: el parámetro cookie '{pname}' no se envía (el Cookie Manager gestiona las cookies)")
    if query:
        spec.path += "?" + "&".join(query)

    body = endpoint.get("body")
    if body and body.get("example") is not None and method not in ("GET", "HEAD"):
        content_type = str(body.get("content_type") or "application/json")
        example = body.get("example")
        if "json" in content_type.lower() or isinstance(example, (dict, list)):
            text, fields, has_user, texts = body_template(example, id_field, numbers, body.get("schema"), spend)
            spec.body, spec.numbers, spec.texts = text, fields, texts
            spec.content_type = content_type if "json" in content_type.lower() else "application/json"
            uses_user = uses_user or has_user
        else:
            spec.body = jmeter_literal(_scalar_text(example))
            spec.content_type = content_type
    spec.uses_user = uses_user
    return spec


# --------------------------------------------------------------------------
# Usuarios
# --------------------------------------------------------------------------

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass
class UserPool:
    """Usuarios de los logs y cuántas sesiones tuvo cada uno (su peso al sortear)."""

    ids: list[str]
    sessions: list[int]


def load_user_pool(requests_csv: Path, limit: int, warnings: list[str]) -> UserPool | None:
    """Los ``limit`` clientes con más sesiones de ``requests.csv``, o ``None`` si no hay ninguno.

    Así las sesiones del plan las llevan los mismos usuarios que en los logs y con
    su misma frecuencia (los pocos muy activos y los muchos ocasionales). Los ids
    con caracteres que cambiarían una ruta o un JSON se saltan.
    """
    seen: dict[str, set[str]] = {}
    with Path(requests_csv).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            client = (row.get("client_id") or "").strip()
            if client:
                seen.setdefault(client, set()).add(row.get("session_id") or "")
    unsafe = [c for c in seen if not _SAFE_ID_RE.fullmatch(c)]
    if unsafe:
        warnings.append(f"{requests_csv}: {len(unsafe)} clientes con caracteres no seguros en el id no entran en el pool")
    ranked = sorted(((len(v), c) for c, v in seen.items() if c not in unsafe), key=lambda t: (-t[0], t[1]))[: min(limit, MAX_POOL_IDS)]
    if not ranked:
        return None
    return UserPool([c for _, c in ranked], [n for n, _ in ranked])


def spend_probability(profile: dict[str, Any], state: str, path: str, method: str, opts: "Options") -> float:
    """Para una retirada, probabilidad de que el importe quepa en el saldo; -1 si no es una retirada.

    Sale del perfil: la tasa de error 4xx del endpoint es, sobre todo, la de
    retiradas sin fondos, así que la parte que sí cabe es ``1 - error_rate``.
    """
    if not opts.balance_aware or method != "POST" or not any(w in path.lower() for w in SPEND_WORDS):
        return -1.0
    if opts.spend_known is not None:
        return opts.spend_known
    rate = _num(_dig(profile, "endpoints", state, "error_rate"))
    if rate is None:
        return DEFAULT_SPEND_KNOWN
    return min(0.99, max(0.5, 1.0 - rate))


# --------------------------------------------------------------------------
# Carga
# --------------------------------------------------------------------------


@dataclass
class Group:
    """Un Thread Group: una hora de la curva o un escalón."""

    label: str
    rate_per_hour: float
    threads: int
    window_s: int
    delay_s: int
    duration_s: int
    session_s: float
    capped: bool = False
    sizing_s: float = 0.0  # duración de sesión con la que se calculan los hilos

    @property
    def rate_s(self) -> float:
        """Sesiones por segundo del grupo: la tasa del proceso de llegadas."""
        return self.rate_per_hour / 3600.0

    @property
    def max_rate_per_hour(self) -> float:
        """Sesiones por hora que caben si cada sesión dura ``sizing_s`` (hilos ocupados todo el tiempo)."""
        return self.threads * 3600.0 / self.sizing_s if self.sizing_s > 0 else math.inf

    @property
    def expected_sessions(self) -> float:
        return self.rate_per_hour * self.window_s / 3600.0


def _dig(doc: dict[str, Any], *keys: str) -> Any:
    """``doc[k1][k2]…`` tolerando niveles ausentes o vacíos: ``(doc.get(k1) or {}).get(k2)…``."""
    value = doc.get(keys[0])
    for key in keys[1:]:
        value = (value or {}).get(key)
    return value


def _num(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def estimate_session_s(profile: dict[str, Any]) -> float:
    """Duración media de sesión estimada: peticiones por sesión, esperas y latencias."""
    length = _num(_dig(profile, "session_length", "requests", "mean")) or 1.0
    think = _num(_dig(profile, "think_time_ms", "global", "mean")) or 0.0
    latency = 0.0
    for info in (profile.get("endpoints") or {}).values():
        share = _num(info.get("share")) or 0.0
        p50 = _num(_dig(info, "latency_ms", "p50")) or 0.0
        latency += share * p50
    return max(0.0, (length - 1.0) * think / 1000.0 + length * latency / 1000.0)


def mean_session_s(profile: dict[str, Any]) -> float:
    """Duración media de sesión del perfil, o la estimada si no está."""
    mean = _num(_dig(profile, "session_length", "duration_s", "mean"))
    return mean if mean is not None and mean > 0 else estimate_session_s(profile)


def mean_requests(profile: dict[str, Any]) -> float:
    """Peticiones medias por sesión del perfil (1 si no está)."""
    mean = _num(_dig(profile, "session_length", "requests", "mean"))
    return mean if mean is not None and mean > 0 else 1.0


def default_tail_s(profile: dict[str, Any]) -> int:
    """Margen para que acaben las sesiones: el mayor de 30 s, 3 veces la media y el p99."""
    p99 = _num(_dig(profile, "session_length", "duration_s", "p99")) or 0.0
    return int(max(MIN_TAIL_S, math.ceil(3.0 * mean_session_s(profile)), math.ceil(p99)))


def thread_count(rate_per_hour: float, session_s: float, max_threads: int, factor: float = 2.0) -> tuple[int, bool]:
    """Hilos de un grupo: ``factor`` veces las sesiones simultáneas de la ley de Little, más uno.

    ``session_s`` es la duración de sesión con la que se dimensiona (con el
    margen de latencia ya sumado). Devuelve el número de hilos, con el tope
    aplicado, y si se ha llegado al tope.
    """
    need = max(1, math.ceil(rate_per_hour / 3600.0 * session_s * factor) + 1)
    return (min(need, max_threads), need > max_threads)


def sizing_session_s(session_s: float, requests: float, latency_margin_s: float) -> float:
    """Duración de sesión para calcular hilos: la medida más ``latency_margin_s`` por petición.

    Si el servidor se satura, cada petición tarda más y las sesiones se
    alargan. Con hilos para sesiones así de largas, la tasa de llegadas se
    mantiene en vez de quedar limitada a hilos / duración real.
    """
    return session_s + max(0.0, requests) * max(0.0, latency_margin_s)


def hour_slots(profile: dict[str, Any]) -> dict[int, dict[str, Any]]:
    hours = _dig(profile, "hourly_curve", "hours") or []
    return {int(h["hour"]): h for h in hours if h.get("hour") is not None}


def plan_curve(
    profile: dict[str, Any],
    *,
    slot_s: int,
    factor: float,
    hours: list[int] | None,
    tail_s: int,
    max_threads: int,
    thread_factor: float = 2.0,
    latency_margin_s: float = 0.0,
) -> tuple[list[Group], list[int]]:
    """Un grupo por hora con sesiones, una detrás de otra, cada una comprimida en ``slot_s`` segundos.

    Las horas sin sesiones no ocupan tiempo en la prueba: se devuelven aparte.
    """
    slots = hour_slots(profile)
    if not slots:
        raise ValueError("el perfil no tiene hourly_curve.hours; usa --mode steps con --base-rate")
    skipped: list[int] = []
    order = hours if hours is not None else sorted(slots)
    groups: list[Group] = []
    fallback = mean_session_s(profile)
    fallback_requests = mean_requests(profile)
    for h in order:
        slot = slots.get(h)
        rate = (_num(slot.get("sessions_per_hour")) or 0.0) if slot else 0.0
        if rate <= 0:
            skipped.append(h)
            continue
        session_s = _num(slot.get("mean_session_duration_s"))
        session_s = session_s if session_s is not None and session_s > 0 else fallback
        requests = (_num(slot.get("requests_per_hour")) or 0.0) / rate
        requests = requests if requests > 0 else fallback_requests
        rate *= factor
        sizing = sizing_session_s(session_s, requests, latency_margin_s)
        threads, capped = thread_count(rate, sizing, max_threads, thread_factor)
        groups.append(
            Group(f"hora {h:02d}", rate, threads, slot_s, len(groups) * slot_s, slot_s + tail_s, session_s, capped, sizing)
        )
    if not groups:
        raise ValueError("ninguna de las horas elegidas tiene sesiones en el perfil")
    return groups, skipped


def format_hours(hours: Sequence[int]) -> str:
    """``[0, 1, 2, 5]`` como ``0-2, 5``."""
    runs: list[list[int]] = []
    for h in hours:
        if runs and h == runs[-1][1] + 1:
            runs[-1][1] = h
        else:
            runs.append([h, h])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def plan_steps(
    profile: dict[str, Any],
    *,
    base_rate: float | None,
    steps: list[float],
    step_s: int,
    factor: float,
    tail_s: int,
    max_threads: int,
    thread_factor: float = 2.0,
    latency_margin_s: float = DEFAULT_LATENCY_MARGIN_STEPS_S,
) -> list[Group]:
    """Escalera para buscar la saturación: la tasa base por cada multiplicador.

    Los hilos se calculan con sesiones alargadas ``latency_margin_s`` por
    petición, para que la tasa de cada escalón se mantenga aunque el servidor
    se sature y responda más despacio.
    """
    if base_rate is None:
        rates = [_num(h.get("sessions_per_hour")) or 0.0 for h in hour_slots(profile).values()]
        base_rate = max(rates, default=0.0)
        if base_rate <= 0:
            raise ValueError("el perfil no tiene sesiones por hora; indica --base-rate")
    session_s = mean_session_s(profile)
    sizing = sizing_session_s(session_s, mean_requests(profile), latency_margin_s)
    groups = []
    for i, mult in enumerate(steps):
        rate = base_rate * mult * factor
        threads, capped = thread_count(rate, sizing, max_threads, thread_factor)
        groups.append(
            Group(f"escalón {i + 1} (x{mult:g})", rate, threads, step_s, i * step_s, step_s + tail_s, session_s, capped, sizing)
        )
    return groups


def format_table(groups: Sequence[Group]) -> str:
    head = (
        f"{'Grupo':<20} {'Sesiones/h':>11} {'Sesiones':>9} {'Sesión s':>9} {'Hilos':>6} "
        f"{'Ventana s':>10} {'Retardo s':>10} {'Duración s':>11}"
    )
    lines = [head, "-" * len(head)]
    for g in groups:
        mark = " (tope)" if g.capped else ""
        lines.append(
            f"{g.label:<20} {g.rate_per_hour:>11.1f} {g.expected_sessions:>9.1f} {g.sizing_s:>9.1f} {g.threads:>6} "
            f"{g.window_s:>10} {g.delay_s:>10} {g.duration_s:>11}{mark}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Modelo que viaja dentro del plan
# --------------------------------------------------------------------------


def _quantiles(dist: Any) -> list[float] | None:
    if not isinstance(dist, dict):
        return None
    q = dist.get("quantiles") or []
    values = [_num(v) for v in q]
    if not values or any(v is None for v in values):
        return None
    return [float(v) for v in values]  # type: ignore[arg-type]


def default_max_steps(profile: dict[str, Any]) -> int:
    longest = _num(_dig(profile, "session_length", "requests", "max")) or 0.0
    return max(MIN_MAX_STEPS, 3 * math.ceil(longest))


def build_model(
    profile: dict[str, Any],
    chain: Chain,
    *,
    pool_size: int,
    max_steps: int,
    numbers: dict[str, list[NumberField]],
    texts: dict[str, list[TextField]] | None = None,
    users: UserPool | None = None,
    spend_fraction: tuple[float, float] = DEFAULT_SPEND_FRACTION,
) -> dict[str, Any]:
    """Modelo que leen los scripts de Groovy: estados por índice, filas acumuladas y esperas."""
    index = {s: i for i, s in enumerate(chain.states)}
    index[END_STATE] = len(chain.states)
    think = profile.get("think_time_ms") or {}
    by_state = think.get("by_state") or {}
    return {
        "version": 1,
        "estados": chain.states,
        "fin": len(chain.states),
        "inicio": cumulative(chain.start, index),
        "filas": [cumulative(chain.rows[s], index) for s in chain.states],
        "espera_global": _quantiles(think.get("global")) or [],
        "espera_por_estado": [_quantiles(by_state.get(s)) for s in chain.states],
        "max_pasos": max_steps,
        "pool": len(users.ids) if users else pool_size,
        "pool_ancho": max(4, len(str(pool_size))),
        "usuarios": users.ids if users else None,
        "usuarios_acum": _accumulate(users.sessions) if users else None,
        "gasto_fraccion": list(spend_fraction),
        "numeros": {k: [f.as_model() for f in v] for k, v in numbers.items()},
        "textos": {k: [f.as_model() for f in v] for k, v in (texts or {}).items()},
    }


def _accumulate(weights: Sequence[float]) -> list[float]:
    """Probabilidad acumulada de un sorteo con esos pesos; el último valor es 1."""
    total = float(sum(weights))
    acc, run = [], 0.0
    for w in weights:
        run += w / total
        acc.append(run)
    acc[-1] = 1.0
    return acc


def model_json(model: dict[str, Any]) -> str:
    """JSON en una línea, ASCII y sin dólares (JMeter no debe interpretarlo)."""
    return json.dumps(model, ensure_ascii=True, separators=(",", ":"), allow_nan=False).replace("$", "\\u0024")


# --------------------------------------------------------------------------
# XML de JMeter
# --------------------------------------------------------------------------


def _clean(text: str) -> str:
    return _INVALID_XML_RE.sub("_", text)


class Node:
    """Elemento de JMeter con sus hijos; se serializa con la estructura hashTree."""

    def __init__(self, tag: str, testclass: str, guiclass: str, name: str) -> None:
        self.el = ET.Element(tag, {"guiclass": guiclass, "testclass": testclass, "testname": _clean(name), "enabled": "true"})
        self.children: list[Node] = []

    def add(self, child: "Node") -> "Node":
        self.children.append(child)
        return child

    def prop(self, kind: str, name: str, value: Any) -> "Node":
        add_prop(self.el, kind, name, value)
        return self


def add_prop(parent: ET.Element, kind: str, name: str, value: Any) -> ET.Element:
    el = ET.SubElement(parent, f"{kind}Prop", {"name": name})
    if kind == "bool":
        el.text = "true" if value else "false"
    else:
        el.text = _clean(str(value))
    return el


def _serialize(nodes: Iterable[Node], tree: ET.Element) -> None:
    for node in nodes:
        tree.append(node.el)
        sub = ET.SubElement(tree, "hashTree")
        _serialize(node.children, sub)


def arguments_prop(parent: ET.Element, name: str, items: list[tuple[str, str]], *, gui: bool = True) -> None:
    """``elementProp`` de tipo Arguments con pares nombre/valor (variables definidas por el usuario)."""
    attrs = {"name": name, "elementType": "Arguments"}
    if gui:
        attrs.update({"guiclass": "ArgumentsPanel", "testclass": "Arguments", "testname": "User Defined Variables", "enabled": "true"})
    ep = ET.SubElement(parent, "elementProp", attrs)
    coll = ET.SubElement(ep, "collectionProp", {"name": "Arguments.arguments"})
    for key, value in items:
        arg = ET.SubElement(coll, "elementProp", {"name": key, "elementType": "Argument"})
        add_prop(arg, "string", "Argument.name", key)
        add_prop(arg, "string", "Argument.value", value)
        add_prop(arg, "string", "Argument.metadata", "=")


def jsr223(tag: str, name: str, script: str, parameters: str = "") -> Node:
    node = Node(tag, tag, "TestBeanGUI", name)
    node.prop("string", "scriptLanguage", "groovy")
    node.prop("string", "parameters", parameters)
    node.prop("string", "filename", "")
    node.prop("string", "cacheKey", "true")
    node.prop("string", "script", script)
    return node


THREAD_GROUP_KINDS = {
    "normal": ("ThreadGroup", "ThreadGroupGui"),
    "setup": ("SetupThreadGroup", "SetupThreadGroupGui"),
    "teardown": ("PostThreadGroup", "PostThreadGroupGui"),
}


def thread_group(name: str, threads: int, *, kind: str, loops: int, duration_s: int | None, delay_s: int) -> Node:
    """Thread Group normal, setUp o tearDown, sin rampa."""
    tag, gui = THREAD_GROUP_KINDS[kind]
    node = Node(tag, tag, gui, name)
    node.prop("int", "ThreadGroup.num_threads", threads)
    node.prop("int", "ThreadGroup.ramp_time", 0)
    node.prop("bool", "ThreadGroup.delayedStart", kind == "normal")
    node.prop("bool", "ThreadGroup.same_user_on_next_iteration", False)
    node.prop("string", "ThreadGroup.on_sample_error", "continue")
    loop = ET.SubElement(
        node.el,
        "elementProp",
        {
            "name": "ThreadGroup.main_controller",
            "elementType": "LoopController",
            "guiclass": "LoopControlPanel",
            "testclass": "LoopController",
            "testname": "Loop Controller",
            "enabled": "true",
        },
    )
    add_prop(loop, "bool", "LoopController.continue_forever", False)
    add_prop(loop, "int", "LoopController.loops", loops)
    node.prop("bool", "ThreadGroup.scheduler", duration_s is not None)
    node.prop("string", "ThreadGroup.duration", "" if duration_s is None else duration_s)
    node.prop("string", "ThreadGroup.delay", "" if duration_s is None else delay_s)
    return node


def header_manager(headers: list[tuple[str, str]]) -> Node:
    node = Node("HeaderManager", "HeaderManager", "HeaderPanel", "Cabeceras")
    coll = ET.SubElement(node.el, "collectionProp", {"name": "HeaderManager.headers"})
    for name, value in headers:
        h = ET.SubElement(coll, "elementProp", {"name": "", "elementType": "Header"})
        add_prop(h, "string", "Header.name", name)
        add_prop(h, "string", "Header.value", value)
    return node


def response_assertion(name: str, pattern: str) -> Node:
    """Aserción sobre el código de respuesta, con "ignorar estado" para que un 4xx pueda pasar."""
    node = Node("ResponseAssertion", "ResponseAssertion", "AssertionGui", name)
    coll = ET.SubElement(node.el, "collectionProp", {"name": "Asserion.test_strings"})
    add_prop(coll, "string", "0", pattern)
    node.prop("string", "Assertion.custom_message", "")
    node.prop("string", "Assertion.test_field", "Assertion.response_code")
    node.prop("bool", "Assertion.assume_success", True)
    node.prop("int", "Assertion.test_type", 1)  # 1 = Matches: la expresión casa con todo el código
    return node


def http_sampler(spec: RequestSpec, label: str) -> Node:
    node = Node("HTTPSamplerProxy", "HTTPSamplerProxy", "HttpTestSampleGui", label)
    node.prop("string", "HTTPSampler.path", spec.path)
    node.prop("string", "HTTPSampler.method", spec.method)
    node.prop("string", "HTTPSampler.contentEncoding", "UTF-8")
    node.prop("bool", "HTTPSampler.follow_redirects", False)
    node.prop("bool", "HTTPSampler.auto_redirects", False)
    node.prop("bool", "HTTPSampler.use_keepalive", True)
    node.prop("bool", "HTTPSampler.DO_MULTIPART_POST", False)
    ep = ET.SubElement(node.el, "elementProp", {"name": "HTTPsampler.Arguments", "elementType": "Arguments"})
    coll = ET.SubElement(ep, "collectionProp", {"name": "Arguments.arguments"})
    if spec.body is not None:
        node.prop("bool", "HTTPSampler.postBodyRaw", True)
        arg = ET.SubElement(coll, "elementProp", {"name": "", "elementType": "HTTPArgument"})
        add_prop(arg, "bool", "HTTPArgument.always_encode", False)
        add_prop(arg, "string", "Argument.value", spec.body)
        add_prop(arg, "string", "Argument.metadata", "=")
    else:
        for name, value in spec.form:
            arg = ET.SubElement(coll, "elementProp", {"name": name, "elementType": "HTTPArgument"})
            add_prop(arg, "bool", "HTTPArgument.always_encode", True)
            add_prop(arg, "string", "Argument.name", name)
            add_prop(arg, "string", "Argument.value", value)
            add_prop(arg, "string", "Argument.metadata", "=")
            add_prop(arg, "bool", "HTTPArgument.use_equals", True)
    headers = list(spec.headers)
    if spec.content_type:
        headers.insert(0, ("Content-Type", spec.content_type))
    if headers:
        node.add(header_manager(headers))
    return node


@dataclass
class Options:
    """Opciones del conversor, ya validadas."""

    mode: str = "curve"
    slot_seconds: int = 60
    factor: float = 1.0
    hours: list[int] | None = None
    base_rate: float | None = None
    steps: list[float] = field(default_factory=lambda: [1.0, 2.0, 5.0, 10.0])
    step_seconds: int = 300
    tail_seconds: int | None = None
    max_threads: int = 500
    thread_factor: float = 2.0
    latency_margin: float | None = None  # None: 5 s por petición en steps, 0 en curve
    pool_size: int = 200
    pool_only: bool = False  # plan que solo da de alta el pool
    create_endpoint: str | None = None  # None: detección automática; "none": sin alta
    id_field: str = "id"
    numbers: dict[str, NumberDist] = field(default_factory=dict)
    max_steps: int | None = None
    seed: int = DEFAULT_SEED
    replay_unmatched: bool = True  # reproducir las rutas de los logs que no están en el jar
    balance_aware: bool = True  # las retiradas son, casi siempre, una parte del saldo visto
    spend_known: float | None = None  # None: 1 - error_rate del endpoint
    spend_fraction: tuple[float, float] = DEFAULT_SPEND_FRACTION

    def latency_margin_s(self) -> float:
        if self.latency_margin is not None:
            return self.latency_margin
        return DEFAULT_LATENCY_MARGIN_STEPS_S if self.mode == "steps" else 0.0


@dataclass
class Plan:
    """Resultado de la conversión: el XML y lo que se informa al usuario."""

    xml: bytes
    groups: list[Group]
    states: list[str]
    dropped: list[str]
    create: str | None
    create_note: str
    model: dict[str, Any]
    warnings: list[str]
    skipped_hours: list[int] = field(default_factory=list)
    pool_filter: str | None = None  # orden que quita las altas del pool de trafico.json


def choose_create(endpoints_doc: dict[str, Any], opts: Options) -> tuple[dict[str, Any] | None, str]:
    """Endpoint de alta y una frase que explica cómo se ha elegido."""
    wanted = opts.create_endpoint
    if wanted is not None and wanted.strip().lower() == "none":
        return None, "sin endpoint de alta (--create-endpoint none)"
    if wanted is not None:
        key = " ".join(wanted.split())
        method, _, path = key.partition(" ")
        for ep in endpoints_doc.get("endpoints") or []:
            if ep.get("id") == key or (str(ep.get("method", "")).upper() == method.upper() and clean_path(str(ep.get("path", ""))) == clean_path(path)):
                return ep, f"{ep.get('id')} (indicado con --create-endpoint)"
        raise ValueError(f"--create-endpoint '{wanted}' no está en endpoints.json")
    candidates = detect_create_endpoint(endpoints_doc, opts.id_field)
    if not candidates:
        return None, "no se ha detectado ningún endpoint de alta; los usuarios del pool no se crean"
    chosen = candidates[0]
    note = f"{chosen.get('id')} (detectado automáticamente)"
    if len(candidates) > 1:
        note += "; otros candidatos: " + ", ".join(str(c.get("id")) for c in candidates[1:])
    return chosen, note


def create_method(endpoint: dict[str, Any]) -> str:
    """Método con el que se da de alta: el del endpoint, o POST si es ANY."""
    method = str(endpoint.get("method") or "POST").upper()
    return "POST" if method == "ANY" else method


def _ere_escape(text: str) -> str:
    """Texto literal dentro de una expresión regular extendida (grep -E, awk)."""
    return re.sub(r"([.\[\]()*+?{}|^$\\])", r"\\\1", text)


def pool_filter_command(
    method: str,
    path: str,
    log: str = "trafico.json",
    out: str = "trafico-sin-pool.json",
) -> str:
    """Orden de awk que quita de ``trafico.json`` las altas del pool.

    Una línea del log es un objeto JSON con las claves ``ts_end``, ``method``,
    ``path``, ``status``, ``latency_ms``, ``client_id`` y ``session_id``, en ese
    orden. Las altas del pool son las del endpoint de alta cuyo cliente no es un
    id creado dentro de una sesión: esos acaban en ``_t<grupo>-<hilo>_<n>``, y
    el resto de ids es del pool, sean los de los logs o ``mjpool_p0001``. Las
    sesiones del setUp desaparecen enteras y el resto del log queda intacto.
    """
    pieces, pos = [], 0
    for m in _PATH_VAR_RE.finditer(path):
        pieces.append(_ere_escape(path[pos : m.start()]))
        pieces.append('[^/"]+')
        pos = m.end()
    pieces.append(_ere_escape(path[pos:]))
    create = (
        f'"method":"{_ere_escape(method)}","path":"{"".join(pieces)}",'
        '"status":[0-9]+,"latency_ms":[0-9.]+,"client_id":"'
    )
    in_session = '"client_id":"[^"]*_t[0-9]+-[0-9]+_[0-9]+",'

    def awk_string(regex: str) -> str:
        return '"' + regex.replace("\\", "\\\\").replace('"', '\\"') + '"'

    program = f"!($0 ~ {awk_string(create)} && $0 !~ {awk_string(in_session)})"
    return "awk '" + program.replace("'", "'\\''") + f"' {log} > {out}"


def convert(profile: dict[str, Any], endpoints_doc: dict[str, Any], opts: Options, users: UserPool | None = None) -> Plan:
    """Construye el plan completo a partir de los dos JSON."""
    warnings: list[str] = []
    states, endpoints, dropped = match_states(profile, endpoints_doc, opts.replay_unmatched)
    for s in dropped:
        warnings.append(f"{s}: no está en endpoints.json; no se reproduce")
    for s in states:
        if endpoints[s].get("_ghost"):
            warnings.append(f"{s}: ruta de los logs que no está en endpoints.json; se reproduce sin parámetros ni cuerpo")
    if not states:
        raise ValueError("ningún estado del perfil tiene endpoint en endpoints.json")
    chain = build_chain(profile, states)
    warnings.extend(chain.warnings)

    create_ep, create_note = choose_create(endpoints_doc, opts)
    if create_ep is not None and not has_id_field(create_ep, opts.id_field):
        warnings.append(f"el endpoint de alta {create_ep.get('id')} no tiene el campo '{opts.id_field}' en el cuerpo")

    specs: list[RequestSpec] = []
    numbers: dict[str, list[NumberField]] = {}
    texts: dict[str, list[TextField]] = {}
    create_index = -1
    for i, s in enumerate(states):
        method, spath = state_method_path(profile, s)
        spend = spend_probability(profile, s, spath, method, opts)
        spec = build_request(s, method, endpoints[s], opts.id_field, opts.numbers, warnings, spend)
        specs.append(spec)
        if spec.numbers:
            numbers[f"s{i}"] = spec.numbers
        if spec.texts:
            texts[f"s{i}"] = spec.texts
        if create_ep is not None and endpoints[s] is create_ep and method == create_method(create_ep):
            create_index = i
    setup_spec = None
    pool_filter = None
    if create_ep is not None:
        method = create_method(create_ep)
        setup_spec = build_request(str(create_ep.get("id")), method, create_ep, opts.id_field, opts.numbers, warnings)
        if setup_spec.numbers:
            numbers["alta"] = setup_spec.numbers
        if setup_spec.texts:
            texts["alta"] = setup_spec.texts
        pool_filter = pool_filter_command(method, clean_path(str(create_ep.get("path") or "/")))
    elif opts.pool_only:
        raise ValueError("--pool-only necesita un endpoint de alta (--create-endpoint)")
    else:
        warnings.append("sin endpoint de alta: las sesiones usan ids del pool que la API quizá no conoce")

    tail = opts.tail_seconds if opts.tail_seconds is not None else default_tail_s(profile)
    skipped: list[int] = []
    groups: list[Group] = []
    if opts.pool_only:
        pass  # el plan solo da de alta el pool: no hay grupos de sesiones
    elif opts.mode == "curve":
        groups, skipped = plan_curve(
            profile,
            slot_s=opts.slot_seconds,
            factor=opts.factor,
            hours=opts.hours,
            tail_s=tail,
            max_threads=opts.max_threads,
            thread_factor=opts.thread_factor,
            latency_margin_s=opts.latency_margin_s(),
        )
    else:
        if opts.hours is not None:
            warnings.append("--hours solo se usa con --mode curve; en la escalera no tiene efecto")
        groups = plan_steps(
            profile,
            base_rate=opts.base_rate,
            steps=opts.steps,
            step_s=opts.step_seconds,
            factor=opts.factor,
            tail_s=tail,
            max_threads=opts.max_threads,
            thread_factor=opts.thread_factor,
            latency_margin_s=opts.latency_margin_s(),
        )
    for g in groups:
        if g.capped:
            warnings.append(
                f"{g.label}: harían falta más de {opts.max_threads} hilos; con el tope caben unas "
                f"{g.max_rate_per_hour:.0f} sesiones/h si cada sesión dura {g.sizing_s:.1f} s, y las llegadas "
                "que no encuentren hilo libre se descartan"
            )

    max_steps = opts.max_steps if opts.max_steps is not None else default_max_steps(profile)
    model = build_model(
        profile,
        chain,
        pool_size=opts.pool_size,
        max_steps=max_steps,
        numbers=numbers,
        texts=texts,
        users=users,
        spend_fraction=opts.spend_fraction,
    )
    xml = render_plan(endpoints_doc, opts, specs, setup_spec, create_index, groups, model, tail)
    return Plan(
        xml=xml,
        groups=groups,
        states=states,
        dropped=dropped,
        create=str(create_ep.get("id")) if create_ep is not None else None,
        create_note=create_note,
        model=model,
        warnings=warnings,
        skipped_hours=skipped,
        pool_filter=pool_filter,
    )


def render_plan(
    endpoints_doc: dict[str, Any],
    opts: Options,
    specs: list[RequestSpec],
    setup_spec: RequestSpec | None,
    create_index: int,
    groups: list[Group],
    model: dict[str, Any],
    tail: int,
) -> bytes:
    """XML del plan para JMeter 5.6.3."""
    jar = _dig(endpoints_doc, "jar", "file") or "?"
    if opts.pool_only:
        comments = (
            f"Generado por morfeo jmeter a partir de profile.json y endpoints.json ({jar}). "
            f"Solo da de alta los {model['pool']} usuarios del pool. "
            "Propiedades: protocol, host, port, pool_prefix, seed, connect_timeout, response_timeout."
        )
        title = "MORFEO: alta del pool"
    else:
        comments = (
            f"Generado por morfeo jmeter a partir de profile.json ({len(specs)} estados) y endpoints.json ({jar}). "
            f"Modo {opts.mode}, factor {opts.factor:g}, cola {tail} s, semilla {opts.seed}. "
            "Propiedades: protocol, host, port, run_prefix, pool_prefix, create_pool, seed, connect_timeout, "
            "response_timeout."
        )
        title = "MORFEO: tráfico sintético"
    plan = Node("TestPlan", "TestPlan", "TestPlanGui", title)
    plan.prop("string", "TestPlan.comments", comments)
    plan.prop("bool", "TestPlan.functional_mode", False)
    plan.prop("bool", "TestPlan.serialize_threadgroups", False)
    plan.prop("bool", "TestPlan.tearDown_on_shutdown", True)
    plan.prop("string", "TestPlan.user_define_classpath", "")
    arguments_prop(
        plan.el,
        "TestPlan.user_defined_variables",
        [
            (VAR_PREFIX, "${__P(run_prefix,jm${__time(yyyyMMddHHmmss,)})}"),
            (VAR_POOL_PREFIX, "${__P(pool_prefix," + DEFAULT_POOL_PREFIX + ")}"),
            (VAR_SEED, "${__P(seed," + str(opts.seed) + ")}"),
            (VAR_MODEL, model_json(model)),
        ],
    )

    defaults = Node("ConfigTestElement", "ConfigTestElement", "HttpDefaultsGui", "Destino (propiedades protocol, host, port)")
    arguments_prop(defaults.el, "HTTPsampler.Arguments", [], gui=False)
    defaults.prop("string", "HTTPSampler.protocol", "${__P(protocol,http)}")
    defaults.prop("string", "HTTPSampler.domain", "${__P(host,localhost)}")
    defaults.prop("string", "HTTPSampler.port", "${__P(port,7070)}")
    defaults.prop("string", "HTTPSampler.connect_timeout", "${__P(connect_timeout,5000)}")
    defaults.prop("string", "HTTPSampler.response_timeout", "${__P(response_timeout,60000)}")
    defaults.prop("string", "HTTPSampler.contentEncoding", "UTF-8")
    defaults.prop("string", "HTTPSampler.implementation", "HttpClient4")
    plan.add(defaults)

    cookies = Node("CookieManager", "CookieManager", "CookiePanel", "Cookies (una sesión por iteración)")
    ET.SubElement(cookies.el, "collectionProp", {"name": "CookieManager.cookies"})
    cookies.prop("bool", "CookieManager.clearEachIteration", True)
    cookies.prop("bool", "CookieManager.controlledByThreadGroup", False)
    cookies.prop("string", "CookieManager.policy", "standard")
    cookies.prop("string", "CookieManager.implementation", "org.apache.jmeter.protocol.http.control.HC4CookieHandler")
    plan.add(cookies)

    if setup_spec is not None:
        threads = max(1, min(SETUP_THREADS, model["pool"]))
        setup = plan.add(
            thread_group(
                f"setUp: alta de {model['pool']} usuarios del pool", threads, kind="setup", loops=1, duration_s=None, delay_s=0
            )
        )
        start = setup.add(flow_action("Preparar altas del pool"))
        start.add(jsr223("JSR223PreProcessor", "Primer id del pool", GROOVY_SETUP_START, f"0 {threads}"))
        loop = setup.add(Node("WhileController", "WhileController", "WhileControllerGui", "Mientras queden altas"))
        loop.prop("string", "WhileController.condition", var_ref(VAR_CONTINUE))
        sampler = loop.add(http_sampler(setup_spec, f"alta pool {setup_spec.name}"))
        if setup_spec.numbers or setup_spec.texts:
            sampler.add(jsr223("JSR223PreProcessor", "Valores del cuerpo", GROOVY_NUMBERS, "alta"))
        sampler.add(response_assertion("Alta correcta (201 o 409)", "201|409"))
        sampler.add(jsr223("JSR223PostProcessor", "Siguiente id del pool", GROOVY_SETUP_NEXT, str(threads)))

    for gi, g in enumerate(groups, start=1):
        tg = plan.add(
            thread_group(
                f"{g.label}: {g.rate_per_hour:.1f} sesiones/h",
                g.threads,
                kind="normal",
                loops=-1,
                duration_s=g.duration_s,
                delay_s=g.delay_s,
            )
        )
        tg.add(response_assertion("2xx, 3xx y 4xx son respuestas válidas; 5xx es fallo", "[234][0-9][0-9]"))
        start = tg.add(flow_action("Inicio de sesión"))
        start.add(jsr223("JSR223PreProcessor", "Usuario y primer estado", GROOVY_SESSION_START, str(gi)))
        start.add(jsr223("JSR223Timer", "Llegada de la sesión (Poisson del grupo)", GROOVY_ARRIVALS, f"{g.rate_s:.9g} {g.window_s}"))
        loop = tg.add(Node("WhileController", "WhileController", "WhileControllerGui", "Peticiones de la sesión"))
        loop.prop("string", "WhileController.condition", var_ref(VAR_CONTINUE))
        loop.add(jsr223("JSR223Timer", "Tiempo de espera (think time)", GROOVY_THINK_TIME))
        loop.add(jsr223("JSR223PostProcessor", "Siguiente estado (Markov)", GROOVY_NEXT_STATE))
        switch = loop.add(Node("SwitchController", "SwitchController", "SwitchControllerGui", "Petición del estado actual"))
        switch.prop("string", "SwitchController.value", var_ref(VAR_STATE))
        for i, spec in enumerate(specs):
            sampler = switch.add(http_sampler(spec, spec.name))
            if i == create_index:
                sampler.add(jsr223("JSR223PreProcessor", "Id nuevo para el alta", GROOVY_NEW_USER))
            if spec.numbers or spec.texts:
                sampler.add(jsr223("JSR223PreProcessor", "Valores del cuerpo", GROOVY_NUMBERS, f"s{i}"))

    if groups:
        # tearDown: resumen de llegadas por grupo. No envía peticiones, así que no
        # deja nada en trafico.json ni en results.jtl.
        post = plan.add(thread_group("tearDown: resumen de llegadas", 1, kind="teardown", loops=1, duration_s=None, delay_s=0))
        summary = post.add(flow_action("Resumen de llegadas"))
        summary.add(jsr223("JSR223PreProcessor", "Sesiones empezadas por grupo", GROOVY_SUMMARY))

    root = ET.Element("jmeterTestPlan", {"version": "1.2", "properties": "5.0", "jmeter": JMETER_VERSION})
    tree = ET.SubElement(root, "hashTree")
    _serialize([plan], tree)
    ET.indent(root, space="  ")
    text = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + text + "\n").encode("utf-8")


def flow_action(name: str) -> Node:
    """Flow Control Action en pausa 0: un paso que no genera muestra, para colgarle scripts y un temporizador."""
    node = Node("TestAction", "TestAction", "TestActionGui", name)
    node.prop("int", "ActionProcessor.action", 1)  # 1 = pausa
    node.prop("int", "ActionProcessor.target", 0)  # 0 = hilo actual
    node.prop("string", "ActionProcessor.duration", "0")
    return node
