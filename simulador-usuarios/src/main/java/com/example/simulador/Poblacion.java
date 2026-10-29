package com.example.simulador;

import java.util.Arrays;
import java.util.SplittableRandom;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Clientes del banco. Unos pocos entran mucho y la mayoría poco (popularidad de
 * Zipf, s = 1), como en el uso real de una aplicación.
 *
 * user_001..user_003 ya existen en la API al arrancar. El resto se dan de alta
 * (POST /api/usuario/crear) al empezar su primera sesión.
 */
public class Poblacion {

    public static final int PRECARGADOS = 3;

    public static final class Usuario {
        private final String id;
        private final AtomicBoolean registrado;

        Usuario(String id, boolean registrado) {
            this.id = id;
            this.registrado = new AtomicBoolean(registrado);
        }

        public String id() {
            return id;
        }

        public boolean registrado() {
            return registrado.get();
        }

        public void marcarRegistrado() {
            registrado.set(true);
        }
    }

    private final Usuario[] usuarios;
    private final double[] acumulada;

    public Poblacion(int n) {
        usuarios = new Usuario[n];
        acumulada = new double[n];
        double total = 0;
        for (int i = 0; i < n; i++) {
            usuarios[i] = new Usuario(String.format("user_%03d", i + 1), i < PRECARGADOS);
            total += 1.0 / (i + 1);
            acumulada[i] = total;
        }
        for (int i = 0; i < n; i++) {
            acumulada[i] /= total;
        }
    }

    public Usuario elegir(SplittableRandom rng) {
        int i = Arrays.binarySearch(acumulada, rng.nextDouble());
        int indice = i >= 0 ? i : -i - 1;
        return usuarios[Math.min(indice, usuarios.length - 1)];
    }

    public int tamano() {
        return usuarios.length;
    }
}
