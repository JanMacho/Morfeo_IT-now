package com.example.simulador;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalTime;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.SplittableRandom;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Genera tráfico HTTP real contra bank-api-stress, imitando a usuarios: llegan
 * sesiones según la hora del día, cada una con su cookie, sus esperas y su recorrido
 * por los endpoints. El resultado es el trafico.json que escribe la API, que sirve de
 * entrada al log_analyzer de MORFEO.
 *
 * Las llegadas de sesiones son un proceso de Poisson cuya tasa sigue la curva horaria.
 * Cada sesión corre en su propio hilo virtual, así que miles de sesiones simultáneas
 * (casi todo el tiempo esperando) cuestan muy poco.
 */
public class SimuladorUsuarios {

    private static final Duration INTERVALO_INFORME = Duration.ofMinutes(1);
    private static final Duration ESPERA_FINAL = Duration.ofMinutes(2);
    // Con tasas bajas (madrugada) se recalcula como mucho cada minuto: el proceso de
    // Poisson no tiene memoria, así que cortar la espera no cambia la distribución.
    private static final double MAX_ESPERA_LLEGADA_S = 60;

    private final Configuracion config;
    private final ClienteApi api;
    private final Poblacion poblacion;
    private final CurvaHoraria curva;
    private final SplittableRandom rng;
    private final AtomicLong sesionesIniciadas = new AtomicLong();
    private final AtomicInteger sesionesActivas = new AtomicInteger();

    public SimuladorUsuarios(Configuracion config) {
        this.config = config;
        this.api = new ClienteApi(config.url());
        this.poblacion = new Poblacion(config.usuarios());
        this.curva = new CurvaHoraria(config.curvaPlana());
        this.rng = new SplittableRandom(config.semilla());
    }

    public static void main(String[] args) throws InterruptedException {
        Configuracion config;
        try {
            if (args.length > 0 && (args[0].equals("--ayuda") || args[0].equals("-h") || args[0].equals("--help"))) {
                System.out.print(Configuracion.USO);
                return;
            }
            config = Configuracion.leer(args);
        } catch (IllegalArgumentException e) {
            System.err.println("Error: " + e.getMessage());
            System.err.print(Configuracion.USO);
            System.exit(2);
            return;
        }

        SimuladorUsuarios simulador = new SimuladorUsuarios(config);
        if (!simulador.api.disponible()) {
            System.err.println("No responde " + config.url() + "/actuator/health. ¿Está arrancada la API?");
            System.exit(1);
        }
        simulador.ejecutar();
    }

    public void ejecutar() throws InterruptedException {
        Instant fin = Instant.now().plus(config.duracion());
        System.out.printf(Locale.ROOT, "Simulando %s contra %s: %.0f sesiones/hora en hora punta (curva %s, zona %s), "
                        + "%d clientes, semilla %d, velocidad x%s%n",
                formatear(config.duracion()), config.url(), config.sesionesHora(),
                config.curvaPlana() ? "plana" : "diaria", config.zona(), poblacion.tamano(), config.semilla(),
                config.velocidad());

        Thread informes = Thread.ofVirtual().start(this::informarPeriodicamente);
        try (ExecutorService sesiones = Executors.newVirtualThreadPerTaskExecutor()) {
            lanzarSesiones(sesiones, fin);
            System.out.println("Fin del periodo: no se abren más sesiones; esperando a las "
                    + sesionesActivas.get() + " que siguen activas (máximo " + formatear(ESPERA_FINAL) + ")");
            sesiones.shutdown();
            if (!sesiones.awaitTermination(ESPERA_FINAL.toSeconds(), TimeUnit.SECONDS)) {
                sesiones.shutdownNow();
            }
        }
        informes.interrupt();
        informar("Final");
    }

    private void lanzarSesiones(ExecutorService sesiones, Instant fin) throws InterruptedException {
        while (true) {
            double porSegundo = config.sesionesHora() * curva.peso(horaLocal()) / 3600.0;
            double espera = -Math.log(1.0 - rng.nextDouble()) / porSegundo;
            boolean llega = espera <= MAX_ESPERA_LLEGADA_S;
            long esperaMs = (long) (Math.min(espera, MAX_ESPERA_LLEGADA_S) * 1000);

            long restanteMs = Duration.between(Instant.now(), fin).toMillis();
            if (esperaMs >= restanteMs) {
                Thread.sleep(Math.max(0, restanteMs));
                return;
            }
            Thread.sleep(esperaMs);
            if (llega) {
                lanzar(sesiones);
            }
        }
    }

    private void lanzar(ExecutorService sesiones) {
        boolean bot = rng.nextDouble() < config.fraccionBots();
        SesionUsuario sesion = new SesionUsuario(api, poblacion.elegir(rng), bot, rng.split(), config.velocidad());
        sesionesIniciadas.incrementAndGet();
        sesionesActivas.incrementAndGet();
        sesiones.submit(() -> {
            try {
                sesion.run();
            } finally {
                sesionesActivas.decrementAndGet();
            }
        });
    }

    private int horaLocal() {
        return ZonedDateTime.now(config.zona()).getHour();
    }

    private void informarPeriodicamente() {
        try {
            while (true) {
                Thread.sleep(INTERVALO_INFORME.toMillis());
                informar(LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm")));
            }
        } catch (InterruptedException e) {
            // fin de la simulación
        }
    }

    private void informar(String etiqueta) {
        System.out.printf("[%s] sesiones: %d iniciadas, %d activas | peticiones: %d | respuestas: %s%n",
                etiqueta, sesionesIniciadas.get(), sesionesActivas.get(), api.peticiones(),
                api.respuestasPorStatus());
    }

    private static String formatear(Duration d) {
        if (d.toSeconds() % 3600 == 0) {
            return d.toHours() + " h";
        }
        if (d.toSeconds() % 60 == 0) {
            return d.toMinutes() + " min";
        }
        return d.toSeconds() + " s";
    }
}
