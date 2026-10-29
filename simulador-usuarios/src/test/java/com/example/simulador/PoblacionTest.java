package com.example.simulador;

import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;
import java.util.SplittableRandom;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PoblacionTest {

    @Test
    void losTresPrimerosYaEstanRegistrados() {
        Poblacion p = new Poblacion(10);
        Map<String, Boolean> registrado = new HashMap<>();
        SplittableRandom rng = new SplittableRandom(1);
        for (int i = 0; i < 10_000; i++) {
            Poblacion.Usuario u = p.elegir(rng);
            registrado.put(u.id(), u.registrado());
        }
        assertTrue(registrado.get("user_001"));
        assertTrue(registrado.get("user_003"));
        assertFalse(registrado.get("user_004"));
        assertEquals(10, registrado.size());
    }

    @Test
    void popularidadDeZipf() {
        Poblacion p = new Poblacion(100);
        Map<String, Integer> visitas = new HashMap<>();
        SplittableRandom rng = new SplittableRandom(1);
        for (int i = 0; i < 100_000; i++) {
            visitas.merge(p.elegir(rng).id(), 1, Integer::sum);
        }
        // Con s = 1, el primero recibe el doble que el segundo y diez veces más que el décimo
        double ratio2 = visitas.get("user_001") / (double) visitas.get("user_002");
        double ratio10 = visitas.get("user_001") / (double) visitas.get("user_010");
        assertEquals(2.0, ratio2, 0.15);
        assertEquals(10.0, ratio10, 1.5);
    }

    @Test
    void curvaHoraria() {
        CurvaHoraria diaria = new CurvaHoraria(false);
        double max = 0;
        for (int h = 0; h < 24; h++) {
            assertTrue(diaria.peso(h) > 0 && diaria.peso(h) <= 1);
            max = Math.max(max, diaria.peso(h));
        }
        assertEquals(1.0, max);
        assertTrue(diaria.peso(10) > 10 * diaria.peso(3));
        assertEquals(1.0, new CurvaHoraria(true).peso(3));
    }
}
