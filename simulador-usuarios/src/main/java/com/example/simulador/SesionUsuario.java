package com.example.simulador;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.SplittableRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Una visita de un cliente: una secuencia de peticiones con esperas entre ellas.
 *
 * Comportamiento:
 *   - Si el cliente aún no existe, la sesión empieza dándolo de alta.
 *   - Después sigue una cadena de Markov entre consultar saldo, ingresar y retirar
 *     (ver TRANSICIONES), hasta que decide irse.
 *   - Las esperas siguen una lognormal cuya mediana depende de la última acción:
 *     tras consultar el saldo se tarda menos que tras rellenar una operación.
 *   - Las retiradas suelen ser de una parte del saldo que el cliente ha visto. A veces
 *     piden más de la cuenta, y la API responde 400 por fondos insuficientes.
 *   - Un 1 % de los envíos lleva un JSON roto (400), como un cliente con fallos.
 *   - Las sesiones de bot no tienen cookie ni cliente: piden rutas inexistentes (404).
 *
 * Todas las peticiones de una sesión llevan su cookie MORFEO_SID, así que en el log
 * comparten ID_SESION.
 */
public class SesionUsuario implements Runnable {

    enum Accion { SALDO, INGRESO, RETIRADA, FIN }

    // Probabilidades de la siguiente acción según la anterior (null = inicio de sesión).
    // Orden de cada fila: SALDO, INGRESO, RETIRADA, FIN.
    private static final double[] DESDE_INICIO = {0.85, 0.08, 0.07, 0.00};
    private static final double[] DESDE_SALDO = {0.10, 0.30, 0.30, 0.30};
    private static final double[] DESDE_INGRESO = {0.45, 0.05, 0.10, 0.40};
    private static final double[] DESDE_RETIRADA = {0.40, 0.10, 0.10, 0.40};

    private static final int MAX_PETICIONES = 30;
    private static final double SIGMA_ESPERA = 0.8;
    private static final double MAX_ESPERA_S = 300;
    private static final double PROB_JSON_ROTO = 0.01;

    private static final List<String> RUTAS_BOT = List.of(
            "/wp-admin", "/wp-login.php", "/.env", "/admin", "/api/v1/saldo/user_001",
            "/phpmyadmin/index.php", "/api/usuarios", "/robots.txt");

    private static final Pattern SALDO = Pattern.compile("\"saldo\"\\s*:\\s*([0-9.Ee+-]+)");

    private final ClienteApi api;
    private final Poblacion.Usuario usuario;
    private final boolean bot;
    private final SplittableRandom rng;
    private final double velocidad;
    private final ClienteApi.Cookie cookie = new ClienteApi.Cookie();

    private BigDecimal saldoConocido;

    public SesionUsuario(ClienteApi api, Poblacion.Usuario usuario, boolean bot, SplittableRandom rng, double velocidad) {
        this.api = api;
        this.usuario = usuario;
        this.bot = bot;
        this.rng = rng;
        this.velocidad = velocidad;
    }

    @Override
    public void run() {
        try {
            if (bot) {
                sesionBot();
            } else {
                sesionCliente();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void sesionBot() throws InterruptedException {
        int n = 1 + rng.nextInt(3);
        for (int i = 0; i < n; i++) {
            api.get(RUTAS_BOT.get(rng.nextInt(RUTAS_BOT.size())), null);
            esperar(0.5);
        }
    }

    private void sesionCliente() throws InterruptedException {
        int peticiones = 0;
        if (!usuario.registrado()) {
            darDeAlta();
            peticiones++;
            esperar(15);
        }

        Accion anterior = null;
        while (peticiones < MAX_PETICIONES) {
            Accion accion = siguiente(anterior);
            if (accion == Accion.FIN) {
                return;
            }
            ejecutar(accion);
            peticiones++;
            esperar(accion == Accion.SALDO ? 6 : 10);
            anterior = accion;
        }
    }

    Accion siguiente(Accion anterior) {
        double[] fila = anterior == null ? DESDE_INICIO
                : switch (anterior) {
                    case SALDO -> DESDE_SALDO;
                    case INGRESO -> DESDE_INGRESO;
                    case RETIRADA -> DESDE_RETIRADA;
                    case FIN -> throw new IllegalStateException();
                };
        double u = rng.nextDouble();
        double acumulado = 0;
        for (int i = 0; i < fila.length; i++) {
            acumulado += fila[i];
            if (u < acumulado) {
                return Accion.values()[i];
            }
        }
        return Accion.FIN;
    }

    private void darDeAlta() {
        BigDecimal inicial = importe(300, 1.0);
        ClienteApi.Respuesta r = api.post("/api/usuario/crear",
                "{\"id\":\"" + usuario.id() + "\",\"saldo_inicial\":" + inicial.toPlainString() + "}", cookie);
        // 409: ya existía (otra sesión suya lo creó a la vez, o la API no se ha reiniciado)
        if (r.status() == 201 || r.status() == 409) {
            usuario.marcarRegistrado();
        }
        if (r.status() == 201) {
            saldoConocido = inicial;
        }
    }

    private void ejecutar(Accion accion) {
        switch (accion) {
            case SALDO -> {
                ClienteApi.Respuesta r = api.get("/api/saldo/" + usuario.id(), cookie);
                if (r.ok()) {
                    leerSaldo(r.cuerpo());
                }
            }
            case INGRESO -> {
                ClienteApi.Respuesta r = api.post("/api/ingreso", movimiento(importe(80, 0.8)), cookie);
                if (r.ok()) {
                    leerSaldo(r.cuerpo());
                }
            }
            case RETIRADA -> {
                ClienteApi.Respuesta r = api.post("/api/retirada", movimiento(importeRetirada()), cookie);
                if (r.ok()) {
                    leerSaldo(r.cuerpo());
                }
            }
            case FIN -> { }
        }
    }

    private String movimiento(BigDecimal cantidad) {
        if (rng.nextDouble() < PROB_JSON_ROTO) {
            return "{\"id\":\"" + usuario.id() + "\",\"cantidad\":}";
        }
        return "{\"id\":\"" + usuario.id() + "\",\"cantidad\":" + cantidad.toPlainString() + "}";
    }

    /** Normalmente una parte del saldo visto; a veces, cualquier cantidad (puede no haber fondos). */
    private BigDecimal importeRetirada() {
        if (saldoConocido != null && saldoConocido.signum() > 0 && rng.nextDouble() < 0.85) {
            double fraccion = 0.05 + 0.5 * rng.nextDouble();
            BigDecimal cantidad = saldoConocido.multiply(BigDecimal.valueOf(fraccion)).setScale(2, RoundingMode.DOWN);
            return cantidad.signum() > 0 ? cantidad : new BigDecimal("0.01");
        }
        return importe(60, 1.0);
    }

    /** Importe lognormal con la mediana dada, redondeado a céntimos y >= 1. */
    private BigDecimal importe(double mediana, double sigma) {
        double valor = Math.max(1, mediana * Math.exp(sigma * rng.nextGaussian()));
        return BigDecimal.valueOf(valor).setScale(2, RoundingMode.HALF_UP);
    }

    private void leerSaldo(String cuerpo) {
        Matcher m = SALDO.matcher(cuerpo);
        if (m.find()) {
            try {
                saldoConocido = new BigDecimal(m.group(1));
            } catch (NumberFormatException ignored) {
                // Respuesta inesperada: se mantiene el último saldo conocido
            }
        }
    }

    /** Espera lognormal con la mediana dada en segundos, acotada y escalada por la velocidad. */
    private void esperar(double medianaS) throws InterruptedException {
        double segundos = Math.min(MAX_ESPERA_S, medianaS * Math.exp(SIGMA_ESPERA * rng.nextGaussian()));
        Thread.sleep((long) (segundos * 1000 / velocidad));
    }
}
