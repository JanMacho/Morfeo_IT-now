package com.example.simulador;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.ZoneId;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ConfiguracionTest {

    @Test
    void valoresPorDefecto() {
        Configuracion c = Configuracion.leer(new String[0]);
        assertEquals("http://localhost:7070", c.url().toString());
        assertEquals(Duration.ofHours(1), c.duracion());
        assertEquals(42, c.semilla());
    }

    @Test
    void leeTodasLasOpciones() {
        Configuracion c = Configuracion.leer(new String[]{
                "--url", "http://api:8080/", "--duracion", "90s", "--sesiones-hora", "500", "--usuarios", "50",
                "--semilla", "7", "--zona", "Europe/Madrid", "--curva", "plana", "--velocidad", "20", "--bots", "0"});
        assertEquals("http://api:8080", c.url().toString());
        assertEquals(Duration.ofSeconds(90), c.duracion());
        assertEquals(500, c.sesionesHora());
        assertEquals(50, c.usuarios());
        assertEquals(7, c.semilla());
        assertEquals(ZoneId.of("Europe/Madrid"), c.zona());
        assertTrue(c.curvaPlana());
        assertEquals(20, c.velocidad());
        assertEquals(0, c.fraccionBots());
    }

    @Test
    void duraciones() {
        assertEquals(Duration.ofMinutes(30), Configuracion.leerDuracion("30m"));
        assertEquals(Duration.ofHours(2), Configuracion.leerDuracion("2h"));
        assertEquals(Duration.ofDays(1), Configuracion.leerDuracion("1D"));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leerDuracion("2x"));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leerDuracion("h"));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leerDuracion("0m"));
    }

    @Test
    void erroresLegibles() {
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leer(new String[]{"--nada", "1"}));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leer(new String[]{"--duracion"}));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leer(new String[]{"--curva", "rara"}));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leer(new String[]{"--bots", "2"}));
        assertThrows(IllegalArgumentException.class, () -> Configuracion.leer(new String[]{"--sesiones-hora", "0"}));
    }
}
