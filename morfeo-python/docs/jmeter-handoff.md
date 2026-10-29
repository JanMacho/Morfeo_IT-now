# Traspaso al bloque JMeter

El plan `.jmx` lo genera `morfeo jmeter` (ver `jmeter.md`) a partir de
`endpoints.json`, `profile.json` y `requests.csv` (formato completo en
`contracts.md`). Esta nota resume qué campo sirve para cada parte del plan, y
sirve también a quien quiera construir el plan por otra vía.

| Parte del plan | Campo de entrada | Notas |
|---|---|---|
| HTTP Request por endpoint | `endpoints.json`: `method`, `path`, `params`, `body.example`, `consumes` | `path` ya incluye `context_path`. Las variables `{id}` se sustituyen por valores de un CSV Data Set o por funciones de JMeter |
| Datos de prueba (IDs de cliente) | `requests.csv`: columna `client_id` | Los logs no guardan cuerpos ni valores de parámetros; el `example` del body es un valor de referencia, no datos reales |
| Orden de peticiones en la sesión | `profile.json`: `transition_matrix` con `START` y `END` | Cada fila suma 1. Si se usa un modelo sin memoria, `baseline.endpoint_mix` da el peso de cada endpoint |
| Tiempo de espera entre peticiones | `think_time_ms.by_state[estado anterior]`, si no `think_time_ms.global` | `quantiles` (101 valores) permite muestrear la distribución empírica; `lognormal` da una alternativa paramétrica |
| Usuarios, ramp-up y duración | `concurrent_users.peak_recommended`, `hourly_curve.hours[].concurrent_users.recommended` | Valores por defecto para `${__P(users,…)}` |
| Curva de carga por hora | `hourly_curve.hours[].sessions_per_hour` y `rps` | Hora local en `hourly_curve.timezone` |
| Errores esperados | `endpoints[id].error_rate` (4xx) | Errores de cliente que forman parte del uso normal: no cuentan como fallo del sistema |
| Errores del servidor | `endpoints[id].server_error_rate` (5xx) | Un 503 (pool de BD saturado) no es comportamiento del usuario: en una prueba de estrés es un resultado que se mide, no un fallo del plan |

**Sesiones y cookies.** El backend identifica la sesión con la cookie
`MORFEO_SID` y da una nueva a cada petición que no la trae. El plan necesita un
*HTTP Cookie Manager*; sin él, cada petición es una sesión distinta en
`trafico.json` y la validación del tráfico generado no tiene sentido.

Un endpoint con `method: "ANY"` en `endpoints.json` (un `@RequestMapping`
sin método en Spring) aparece en `profile.json` con el método real de los logs:
`ANY /x` en el jar puede ser `GET /x` y `POST /x` en el perfil. Para unirlos,
comparad la ruta y aceptad cualquier método cuando el del jar sea `ANY`.

Los estados con `matched: false` son rutas de los logs que no casan con ningún
endpoint del jar (por ejemplo, 404 a rutas inexistentes). Conviene decidir en
el bloque JMeter si se reproducen o se descartan.
