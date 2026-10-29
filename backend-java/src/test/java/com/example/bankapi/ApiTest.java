package com.example.bankapi;

import com.example.bankapi.logging.TrafficLogger;
import com.example.bankapi.repository.BancoRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.annotation.DirtiesContext;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.math.BigDecimal;
import java.net.CookieManager;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Levanta la aplicación real en un puerto libre, contra un PostgreSQL en Docker, le
 * hace peticiones HTTP y comprueba las respuestas y el trafico.json resultante. Cada
 * test arranca un contexto nuevo (fichero de log limpio) y deja la tabla como en
 * db/reset.sql, porque los datos de la BD sobreviven al contexto.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@DirtiesContext(classMode = DirtiesContext.ClassMode.AFTER_EACH_TEST_METHOD)
class ApiTest extends PostgresCompartido {

    // Formato que espera el log_analyzer de MORFEO (JSON Lines): un objeto por línea, con las
    // siete claves en este orden.
    private static final List<String> CLAVES_LOG =
            List.of("ts_end", "method", "path", "status", "latency_ms", "client_id", "session_id");
    private static final Pattern LINEA_LOG = Pattern.compile(
            "^\\{\"ts_end\":\"\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}\\.\\d{6}Z\",\"method\":\"[A-Z]+\","
                    + "\"path\":\"(?:[^\"\\\\]|\\\\.)+\",\"status\":\\d{3},\"latency_ms\":\\d+\\.\\d{3},"
                    + "\"client_id\":\"(?:[^\"\\\\]|\\\\.)+\",\"session_id\":\"[0-9a-f]{32}\"\\}$");
    private static final Pattern VALOR_JSON = Pattern.compile("(\"(?:[^\"\\\\]|\\\\.)*\"|[^,}]*)");

    @TempDir
    static Path tmp;

    @DynamicPropertySource
    static void ficheroLogPropio(DynamicPropertyRegistry registro) {
        // El proveedor se evalúa cada vez que se lee la propiedad: el test pregunta la
        // ruta al propio logger (getFichero()) en lugar de leerla otra vez
        registro.add("trafico.log.path", () -> tmp.resolve("trafico-" + UUID.randomUUID() + ".json").toString());
    }

    @LocalServerPort
    int puerto;

    @Autowired
    BancoRepository repositorio;

    @Autowired
    TrafficLogger logger;

    @Autowired
    JdbcTemplate jdbc;

    private HttpClient http;

    @BeforeEach
    void cliente() {
        resetear(jdbc);
        http = HttpClient.newHttpClient();
    }

    private URI uri(String ruta) {
        return URI.create("http://localhost:" + puerto + ruta);
    }

    private HttpResponse<String> get(String ruta) throws Exception {
        return http.send(HttpRequest.newBuilder(uri(ruta)).GET().build(), HttpResponse.BodyHandlers.ofString());
    }

    private HttpRequest post(String ruta, String json) {
        return HttpRequest.newBuilder(uri(ruta))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();
    }

    private HttpResponse<String> enviar(String ruta, String json) throws Exception {
        return http.send(post(ruta, json), HttpResponse.BodyHandlers.ofString());
    }

    /** Vacía la cola del logger para que todas las líneas estén en el fichero. */
    private List<String> lineasDelLog() throws Exception {
        logger.shutdown();
        return Files.readAllLines(logger.getFichero(), StandardCharsets.UTF_8);
    }

    private static String campo(String linea, int n) {
        String clave = "\"" + CLAVES_LOG.get(n) + "\":";
        int inicio = linea.indexOf(clave);
        assertTrue(inicio >= 0, "falta la clave " + CLAVES_LOG.get(n) + " en " + linea);
        Matcher m = VALOR_JSON.matcher(linea);
        assertTrue(m.find(inicio + clave.length()), linea);
        String valor = m.group(1);
        return valor.startsWith("\"") ? valor.substring(1, valor.length() - 1) : valor;
    }

    @Test
    void endpointsBasicos() throws Exception {
        HttpResponse<String> saldo = get("/api/saldo/user_001");
        assertEquals(200, saldo.statusCode());
        assertEquals("{\"id\":\"user_001\",\"saldo\":1000.00}", saldo.body());

        assertEquals(404, get("/api/saldo/nadie").statusCode());
        assertEquals(200, enviar("/api/ingreso", "{\"id\":\"user_001\",\"cantidad\":100.50}").statusCode());
        assertEquals(200, enviar("/api/retirada", "{\"id\":\"user_001\",\"cantidad\":50}").statusCode());
        assertEquals(400, enviar("/api/retirada", "{\"id\":\"user_002\",\"cantidad\":999999}").statusCode());
        assertEquals(404, enviar("/api/ingreso", "{\"id\":\"nadie\",\"cantidad\":1}").statusCode());
        assertEquals(201, enviar("/api/usuario/crear", "{\"id\":\"user_004\",\"saldo_inicial\":300}").statusCode());
        assertEquals(409, enviar("/api/usuario/crear", "{\"id\":\"user_004\",\"saldo_inicial\":300}").statusCode());

        assertEquals(new BigDecimal("1050.50"), repositorio.saldo("user_001").orElseThrow());

        // El usuario creado se puede usar desde ese momento
        assertEquals("{\"id\":\"user_004\",\"saldo\":300}", get("/api/saldo/user_004").body());
        assertEquals("{\"id\":\"user_004\",\"saldo\":310.25}",
                enviar("/api/ingreso", "{\"id\":\"user_004\",\"cantidad\":10.25}").body());
        assertEquals("{\"error\":\"Fondos insuficientes\",\"id\":\"user_004\",\"saldo_actual\":310.25}",
                enviar("/api/retirada", "{\"id\":\"user_004\",\"cantidad\":1000}").body());
    }

    @Test
    void erroresDeFormatoSon4xxYNo500() throws Exception {
        assertEquals(400, enviar("/api/ingreso", "{esto no es json").statusCode());
        assertEquals(400, enviar("/api/ingreso", "").statusCode());
        assertEquals(400, enviar("/api/ingreso", "null").statusCode());
        assertEquals(400, enviar("/api/ingreso", "{\"id\":\"user_001\",\"cantidad\":\"mucho\"}").statusCode());
        assertEquals(400, enviar("/api/ingreso", "{\"cantidad\":5}").statusCode());
        assertEquals(400, enviar("/api/retirada", "{\"id\":\"user_001\",\"cantidad\":-5}").statusCode());
        assertEquals(400, enviar("/api/usuario/crear", "null").statusCode());
        assertEquals(404, get("/ruta/que/no/existe").statusCode());
        assertEquals(405, get("/api/ingreso").statusCode());
        assertEquals(415, http.send(HttpRequest.newBuilder(uri("/api/ingreso"))
                .POST(HttpRequest.BodyPublishers.ofString("{\"id\":\"user_001\",\"cantidad\":1}")).build(),
                HttpResponse.BodyHandlers.ofString()).statusCode());

        HttpResponse<String> r = enviar("/api/ingreso", "{esto no es json");
        assertFalse(r.body().contains("Unexpected character"), "no debe filtrar el detalle interno: " + r.body());
    }

    @Test
    void cadaPeticionDejaUnaLineaConElFormatoDelContrato() throws Exception {
        get("/api/saldo/user_001");
        enviar("/api/ingreso", "{\"id\":\"user_002\",\"cantidad\":1}");
        enviar("/api/ingreso", "{esto no es json");
        get("/ruta/que/no/existe");
        // Intento de inyección en el log a través del id
        enviar("/api/ingreso", "{\"id\":\"x|y\\n2026-01-01T00:00:00.000000Z | GET | /falso | 200 | 1.000 | z\",\"cantidad\":1}");

        List<String> lineas = lineasDelLog();
        assertEquals(5, lineas.size(), String.join("\n", lineas));
        for (String linea : lineas) {
            assertTrue(LINEA_LOG.matcher(linea).matches(), "línea fuera de formato: " + linea);
        }
        assertEquals("GET | /api/saldo/user_001 | 200", String.join(" | ", campo(lineas.get(0), 1), campo(lineas.get(0), 2), campo(lineas.get(0), 3)));
        assertEquals("user_001", campo(lineas.get(0), 5));
        assertEquals("user_002", campo(lineas.get(1), 5));
        assertEquals("400", campo(lineas.get(2), 3));
        assertEquals("404", campo(lineas.get(3), 3));
        assertEquals("UNKNOWN", campo(lineas.get(3), 5));
        assertEquals("404", campo(lineas.get(4), 3));
        // El salto de línea del id va escapado: la línea sigue siendo un solo objeto JSON
        assertTrue(lineas.get(4).contains("\"client_id\":\"x|y\\u000a2026-01-01T00:00:00.000000Z | GET | /falso | 200 | 1.000 | z\""), lineas.get(4));
    }

    @Test
    void laSesionSeMantieneConLaCookieYCambiaSinElla() throws Exception {
        // Un cliente con cookies (como JMeter con HTTP Cookie Manager) mantiene su sesión
        HttpClient conCookies = HttpClient.newBuilder().cookieHandler(new CookieManager()).build();
        HttpResponse<String> primera = conCookies.send(HttpRequest.newBuilder(uri("/api/saldo/user_001")).build(),
                HttpResponse.BodyHandlers.ofString());
        String setCookie = primera.headers().firstValue("Set-Cookie").orElse("");
        assertTrue(setCookie.startsWith("MORFEO_SID="), setCookie);
        assertTrue(setCookie.contains("Max-Age=1800"), setCookie);
        assertTrue(setCookie.contains("HttpOnly"), setCookie);
        conCookies.send(HttpRequest.newBuilder(uri("/api/saldo/user_002")).build(), HttpResponse.BodyHandlers.ofString());

        // Un cliente sin cookies abre una sesión por petición
        get("/api/saldo/user_001");
        get("/api/saldo/user_001");

        // Una cookie manipulada se sustituye por una sesión nueva
        http.send(HttpRequest.newBuilder(uri("/api/saldo/user_001"))
                .header("Cookie", "MORFEO_SID=abc|def").build(), HttpResponse.BodyHandlers.ofString());

        List<String> sesiones = lineasDelLog().stream().map(l -> campo(l, 6)).toList();
        assertEquals(5, sesiones.size());
        assertEquals(sesiones.get(0), sesiones.get(1));
        assertNotEquals(sesiones.get(1), sesiones.get(2));
        assertNotEquals(sesiones.get(2), sesiones.get(3));
        assertTrue(sesiones.get(4).matches("[0-9a-f]{32}"), sesiones.get(4));
    }

    @Test
    void elHealthcheckNoApareceEnElLog() throws Exception {
        HttpResponse<String> salud = get("/actuator/health");
        assertEquals(200, salud.statusCode());
        assertTrue(salud.body().contains("UP"), salud.body());
        get("/api/saldo/user_001");

        List<String> lineas = lineasDelLog();
        assertEquals(1, lineas.size(), String.join("\n", lineas));
        assertTrue(lineas.get(0).contains("/api/saldo/user_001"));
    }

    @Test
    void lasMetricasDeLasColasRespondenYNoSeRegistran() throws Exception {
        get("/api/saldo/user_001"); // abre el pool de conexiones
        for (String metrica : List.of("hikaricp.connections.pending", "hikaricp.connections.active",
                "tomcat.threads.busy")) {
            HttpResponse<String> r = get("/actuator/metrics/" + metrica);
            assertEquals(200, r.statusCode(), metrica + ": " + r.body());
            assertTrue(r.body().contains("\"name\":\"" + metrica + "\""), r.body());
        }
        assertEquals(200, get("/actuator/health/liveness").statusCode());
        assertEquals(200, get("/actuator/health/readiness").statusCode());

        List<String> lineas = lineasDelLog();
        assertEquals(1, lineas.size(), String.join("\n", lineas));
    }

    @Test
    void retiradasConcurrentesPorHttp() throws Exception {
        // user_002 tiene 500 €: de 1.000 retiradas de 1 € solo pueden salir 500.
        // 32 clientes a la vez: abrir 1.000 conexiones de golpe satura la cola de
        // conexiones del sistema operativo y el fallo sería del test, no del servidor.
        ExecutorService clientes = Executors.newFixedThreadPool(32);
        List<Future<Integer>> respuestas = new ArrayList<>();
        for (int i = 0; i < 1_000; i++) {
            respuestas.add(clientes.submit(() -> enviar("/api/retirada", "{\"id\":\"user_002\",\"cantidad\":1}").statusCode()));
        }
        List<Integer> estados = new ArrayList<>();
        for (Future<Integer> r : respuestas) {
            estados.add(r.get());
        }
        clientes.shutdown();
        long ok = estados.stream().filter(s -> s == 200).count();
        long sinFondos = estados.stream().filter(s -> s == 400).count();

        assertEquals(500, ok);
        assertEquals(500, sinFondos);
        assertEquals(0, repositorio.saldo("user_002").orElseThrow().compareTo(BigDecimal.ZERO));
        assertEquals(1_000, lineasDelLog().size());
    }
}
