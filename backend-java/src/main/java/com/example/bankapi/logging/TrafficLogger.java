package com.example.bankapi.logging;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Logger de tráfico ASÍNCRONO y THREAD-SAFE hacia el fichero físico "trafico.json".
 *
 * Diseño (patrón productor-consumidor):
 *   - Cada petición HTTP actúa como PRODUCTOR: simplemente ofrece una línea de texto a
 *     una BlockingQueue. Esta operación es no bloqueante y extremadamente rápida, por lo
 *     que cientos/miles de hilos de petición de JMeter pueden invocarla concurrentemente
 *     sin apenas coste ni contención.
 *   - Un único hilo dedicado actúa como CONSUMIDOR: vacía la cola por lotes y escribe
 *     cada línea en trafico.json.
 *
 * Ventajas de este enfoque frente a que cada hilo HTTP escriba directamente al fichero:
 *   1. Se evita contención de I/O en disco bajo carga alta (un único escritor secuencial).
 *   2. Se evita cualquier condición de carrera sobre el fichero (solo un hilo lo abre/escribe).
 *   3. La latencia de las peticiones HTTP no depende de la latencia de disco.
 *   4. No depende de la consola de Docker: escribe directamente en el sistema de ficheros
 *      local, cumpliendo el requisito de trazabilidad física.
 *
 * Bajo carga sostenida:
 *   - La cola está acotada. Si el disco no da abasto y se llena, la línea se descarta y se
 *     cuenta (getDescartadas()) en lugar de crecer hasta un OutOfMemoryError.
 *   - El flush se hace al vaciar la cola, no en cada línea: con tráfico bajo cada línea
 *     llega al disco al momento, y con tráfico alto se escribe en bloques.
 *   - Si falla la escritura (disco lleno, volumen desmontado...), el hilo escritor no
 *     muere: avisa por consola, descarta el lote y vuelve a abrir el fichero.
 *
 * Formato (contrato con el log_analyzer de MORFEO, formato "jsonl"): una línea por
 * petición, que es un objeto JSON con estas claves y en este orden:
 *   {"ts_end":"2026-10-01T09:25:34.548091Z","method":"POST","path":"/api/retirada",
 *    "status":400,"latency_ms":0.734,"client_id":"user_002","session_id":"4ce8..."}
 * "ts_end" es el FIN de la petición, en UTC con microsegundos. client_id y session_id
 * valen "UNKNOWN" si no hay. El fichero es JSON Lines: no es un único array, para poder
 * añadir líneas sin reescribirlo.
 *
 * No depende de Spring: es un objeto normal que la aplicación declara como bean.
 */
public class TrafficLogger {

    public static final int CAPACIDAD_POR_DEFECTO = 100_000;

    private static final int LOTE_MAXIMO = 1_000;
    private static final long ESPERA_REINTENTO_MS = 1_000;

    private static final DateTimeFormatter FORMATO_TIMESTAMP =
            DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSS'Z'").withZone(ZoneOffset.UTC);

    private final Path fichero;
    private final BlockingQueue<String> cola;
    private final AtomicLong descartadas = new AtomicLong();
    private final Thread hiloEscritor;
    private volatile boolean activo = true;

    public TrafficLogger(Path fichero) {
        this(fichero, CAPACIDAD_POR_DEFECTO);
    }

    public TrafficLogger(Path fichero, int capacidad) {
        this.fichero = fichero;
        this.cola = new LinkedBlockingQueue<>(capacidad);
        hiloEscritor = new Thread(this::procesarCola, "traffic-log-writer");
        hiloEscritor.setDaemon(true);
        hiloEscritor.start();
    }

    /**
     * Construye una línea JSON con el formato del log de tráfico. Los textos vienen de la
     * petición (ruta, cliente, sesión) y se escapan para que sigan siendo JSON válido y
     * ocupen una sola línea aunque traigan comillas, barras o saltos de línea.
     */
    public static String formatear(Instant fin, String metodo, String endpoint, int status,
                                   double tiempoMs, String idCliente, String idSesion) {
        return String.format(Locale.ROOT,
                "{\"ts_end\":\"%s\",\"method\":%s,\"path\":%s,\"status\":%d,\"latency_ms\":%.3f,"
                        + "\"client_id\":%s,\"session_id\":%s}",
                FORMATO_TIMESTAMP.format(fin),
                textoJson(metodo),
                textoJson(endpoint),
                status,
                tiempoMs,
                textoJson(idCliente),
                textoJson(idSesion));
    }

    /** Valor de texto como literal JSON (con comillas). Vacío o nulo es "UNKNOWN". */
    static String textoJson(String valor) {
        String texto = (valor == null || valor.isBlank()) ? "UNKNOWN" : valor.strip();
        StringBuilder sb = new StringBuilder(texto.length() + 2).append('"');
        for (int i = 0; i < texto.length(); i++) {
            char c = texto.charAt(i);
            if (c == '"') {
                sb.append("\\\"");
            } else if (c == '\\') {
                sb.append("\\\\");
            } else if (c < 0x20 || c == 0x7f || c == 0x2028 || c == 0x2029) {
                sb.append(String.format("\\u%04x", (int) c));
            } else {
                sb.append(c);
            }
        }
        return sb.append('"').toString();
    }

    /**
     * Encola una línea de log. Método no bloqueante y seguro para ser invocado
     * concurrentemente desde cualquier número de hilos de petición.
     */
    public void log(String linea) {
        if (!cola.offer(linea) && descartadas.getAndIncrement() == 0) {
            System.err.println("Cola de " + fichero + " llena: se empiezan a descartar líneas de tráfico");
        }
    }

    public Path getFichero() {
        return fichero;
    }

    /** Líneas que no llegaron al fichero (cola llena o error de escritura). */
    public long getDescartadas() {
        return descartadas.get();
    }

    private void procesarCola() {
        List<String> lote = new ArrayList<>(LOTE_MAXIMO);
        while (activo || !cola.isEmpty()) {
            try (BufferedWriter writer = Files.newBufferedWriter(fichero, StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND)) {
                while (activo || !cola.isEmpty()) {
                    String primera = cola.poll(500, TimeUnit.MILLISECONDS);
                    if (primera == null) {
                        continue;
                    }
                    lote.add(primera);
                    cola.drainTo(lote, LOTE_MAXIMO - 1);
                    for (String linea : lote) {
                        writer.write(linea);
                        writer.newLine();
                    }
                    lote.clear();
                    if (cola.isEmpty()) {
                        writer.flush(); // trazabilidad en tiempo real cuando la carga lo permite
                    }
                }
            } catch (IOException e) {
                descartadas.addAndGet(lote.size());
                lote.clear();
                System.err.println("Error escribiendo en " + fichero + ": " + e.getMessage()
                        + ". Se reintenta en " + ESPERA_REINTENTO_MS + " ms");
                if (!esperar(ESPERA_REINTENTO_MS)) {
                    return;
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }

    private static boolean esperar(long ms) {
        try {
            Thread.sleep(ms);
            return true;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    /**
     * Apagado ordenado: deja de esperar nuevas líneas y vacía la cola pendiente.
     * Se invoca desde un shutdown hook al detener la aplicación.
     */
    public void shutdown() {
        activo = false;
        try {
            hiloEscritor.join(5000);
        } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
        long perdidas = descartadas.get();
        if (perdidas > 0) {
            System.err.println("Líneas de tráfico descartadas durante la ejecución: " + perdidas);
        }
    }
}
