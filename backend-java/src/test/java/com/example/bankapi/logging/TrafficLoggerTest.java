package com.example.bankapi.logging;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import java.util.Locale;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TrafficLoggerTest {

    @TempDir
    Path tmp;

    @Test
    void formateaUnObjetoJsonConSieteClavesYTimestampDeAnchoFijo() {
        String linea = TrafficLogger.formatear(Instant.parse("2026-09-28T15:10:38Z"),
                "POST", "/api/ingreso", 200, 0.4123, "user_001", "s1");
        assertEquals("{\"ts_end\":\"2026-09-28T15:10:38.000000Z\",\"method\":\"POST\",\"path\":\"/api/ingreso\","
                + "\"status\":200,\"latency_ms\":0.412,\"client_id\":\"user_001\",\"session_id\":\"s1\"}", linea);
    }

    @Test
    void elTimestampLlevaSiempreMicrosegundos() {
        String linea = TrafficLogger.formatear(Instant.parse("2026-09-28T15:10:25.242960900Z"),
                "GET", "/x", 200, 1, "a", "s");
        assertTrue(linea.startsWith("{\"ts_end\":\"2026-09-28T15:10:25.242960Z\","), linea);
    }

    @Test
    void usaPuntoDecimalAunqueElLocaleSeaEspanol() {
        Locale anterior = Locale.getDefault();
        Locale.setDefault(new Locale("es", "ES"));
        try {
            String linea = TrafficLogger.formatear(Instant.EPOCH, "GET", "/x", 200, 12.5, "a", "s");
            assertTrue(linea.contains("\"latency_ms\":12.500,"), linea);
        } finally {
            Locale.setDefault(anterior);
        }
    }

    @Test
    void escapaEndpointClienteYSesionYSigueSiendoUnaSolaLinea() {
        String linea = TrafficLogger.formatear(Instant.EPOCH, "POST", "/api/a|b\"c\\d", 400, 1,
                "evil\"id\n{\"falso\":1}", "s\r\n\u0001");
        assertEquals(1, linea.lines().count(), linea);
        assertTrue(linea.contains("\"path\":\"/api/a|b\\\"c\\\\d\""), linea);
        assertTrue(linea.contains("\"client_id\":\"evil\\\"id\\u000a{\\\"falso\\\":1}\""), linea);
        assertTrue(linea.contains("\"session_id\":\"s\\u000d\\u000a\\u0001\""), linea);
    }

    @Test
    void idVacioONuloSeEscribeComoUnknown() {
        assertTrue(TrafficLogger.formatear(Instant.EPOCH, "GET", "/x", 404, 1, null, "s")
                .contains("\"client_id\":\"UNKNOWN\""));
        assertTrue(TrafficLogger.formatear(Instant.EPOCH, "GET", "/x", 404, 1, "  ", "s")
                .contains("\"client_id\":\"UNKNOWN\""));
    }

    @Test
    void elApagadoVaciaLaColaEnElFichero() throws Exception {
        Path fichero = tmp.resolve("trafico.json");
        TrafficLogger logger = new TrafficLogger(fichero);
        for (int i = 0; i < 10_000; i++) {
            logger.log("linea " + i);
        }
        logger.shutdown();

        List<String> lineas = Files.readAllLines(fichero, StandardCharsets.UTF_8);
        assertEquals(10_000, lineas.size());
        assertEquals("linea 0", lineas.get(0));
        assertEquals("linea 9999", lineas.get(9_999));
        assertEquals(0, logger.getDescartadas());
    }

    @Test
    void escribeEnUtf8() throws Exception {
        Path fichero = tmp.resolve("trafico.json");
        TrafficLogger logger = new TrafficLogger(fichero);
        logger.log("año_ñandú");
        logger.shutdown();
        assertEquals(List.of("año_ñandú"), Files.readAllLines(fichero, StandardCharsets.UTF_8));
    }

    @Test
    void conLaColaLlenaDescartaYCuenta() throws Exception {
        // Un directorio en lugar de un fichero: la escritura falla siempre y la cola se
        // llena. El logger no debe bloquear a quien llama ni crecer sin límite.
        Path directorio = Files.createDirectory(tmp.resolve("no-es-un-fichero"));
        TrafficLogger logger = new TrafficLogger(directorio, 10);
        for (int i = 0; i < 1_000; i++) {
            logger.log("linea " + i);
        }
        // El fichero nunca llega a abrirse, así que nadie saca líneas de la cola
        assertEquals(990, logger.getDescartadas());
    }
}
