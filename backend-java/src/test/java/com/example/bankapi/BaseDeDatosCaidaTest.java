package com.example.bankapi;

import com.example.bankapi.logging.TrafficLogger;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * La aplicación arrancada contra un puerto donde no hay base de datos: las peticiones
 * deben responder 503 (no 500) y quedar en el log con ese código, y el pod debe seguir
 * vivo (liveness UP) pero fuera del balanceo (readiness DOWN).
 *
 * No necesita Docker. spring.sql.init.mode=never porque, si no, el arranque fallaría
 * al ejecutar schema.sql.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT, properties = {
        "spring.datasource.url=jdbc:postgresql://localhost:1/banco",
        "spring.datasource.hikari.connection-timeout=500",
        "spring.sql.init.mode=never"
})
class BaseDeDatosCaidaTest {

    @TempDir
    static Path tmp;

    @DynamicPropertySource
    static void ficheroLogPropio(DynamicPropertyRegistry registro) {
        registro.add("trafico.log.path", () -> tmp.resolve("trafico.json").toString());
    }

    @LocalServerPort
    int puerto;

    @Autowired
    TrafficLogger logger;

    private final HttpClient http = HttpClient.newHttpClient();

    private HttpResponse<String> get(String ruta) throws Exception {
        return http.send(HttpRequest.newBuilder(URI.create("http://localhost:" + puerto + ruta)).GET().build(),
                HttpResponse.BodyHandlers.ofString());
    }

    @Test
    void sinBaseDeDatosRespondeServicioNoDisponible() throws Exception {
        HttpResponse<String> saldo = get("/api/saldo/user_001");
        assertEquals(503, saldo.statusCode());
        assertEquals("{\"error\":\"Base de datos no disponible\"}", saldo.body());

        HttpResponse<String> ingreso = http.send(HttpRequest.newBuilder(URI.create("http://localhost:" + puerto + "/api/ingreso"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"id\":\"user_001\",\"cantidad\":1}")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(503, ingreso.statusCode());

        // Los errores de validación no tocan la BD y siguen siendo 400
        HttpResponse<String> invalida = http.send(HttpRequest.newBuilder(URI.create("http://localhost:" + puerto + "/api/ingreso"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"id\":\"user_001\",\"cantidad\":-1}")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(400, invalida.statusCode());

        logger.shutdown();
        List<String> lineas = Files.readAllLines(logger.getFichero(), StandardCharsets.UTF_8);
        assertEquals(3, lineas.size(), String.join("\n", lineas));
        assertTrue(lineas.get(0).contains("\"method\":\"GET\",\"path\":\"/api/saldo/user_001\",\"status\":503,"), lineas.get(0));
        assertTrue(lineas.get(1).contains("\"method\":\"POST\",\"path\":\"/api/ingreso\",\"status\":503,"), lineas.get(1));
    }

    @Test
    void elPodSigueVivoPeroNoRecibeTrafico() throws Exception {
        assertEquals(200, get("/actuator/health/liveness").statusCode());
        assertEquals(503, get("/actuator/health/readiness").statusCode());
        // /actuator/health (el que usa el simulador) sigue respondiendo, con el estado DOWN
        HttpResponse<String> salud = get("/actuator/health");
        assertEquals(503, salud.statusCode());
        assertTrue(salud.body().contains("DOWN"), salud.body());
    }
}
