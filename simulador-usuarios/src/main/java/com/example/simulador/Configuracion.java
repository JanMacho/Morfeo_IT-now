package com.example.simulador;

import java.net.URI;
import java.time.Duration;
import java.time.ZoneId;
import java.util.Locale;

/**
 * Opciones de línea de comandos del simulador.
 *
 * @param url          URL base de la API (sin barra final)
 * @param duracion     cuánto tiempo se lanzan sesiones nuevas
 * @param sesionesHora sesiones por hora en la hora punta; el resto de horas, según la curva
 * @param usuarios     número de clientes distintos (incluye user_001..user_003)
 * @param semilla      semilla de los números aleatorios
 * @param zona         zona horaria en la que se aplica la curva horaria
 * @param curvaPlana   true para la misma tasa de sesiones a todas horas
 * @param velocidad    divide los tiempos de espera entre peticiones (1 = tiempo real)
 * @param fraccionBots fracción de sesiones que son bots que piden rutas inexistentes
 */
public record Configuracion(URI url, Duration duracion, double sesionesHora, int usuarios, long semilla,
                            ZoneId zona, boolean curvaPlana, double velocidad, double fraccionBots) {

    public static final String USO = """
            Uso: java -jar simulador-usuarios.jar [opciones]

              --url URL             API a la que se envía el tráfico (http://localhost:7070)
              --duracion D          tiempo lanzando sesiones: 90s, 30m, 2h, 1d (1h)
              --sesiones-hora N     sesiones por hora en la hora punta (60)
              --usuarios N          clientes distintos, incluidos user_001..user_003 (200)
              --semilla N           semilla aleatoria (42)
              --zona ZONA           zona horaria de la curva diaria (la del sistema)
              --curva diaria|plana  tasa de sesiones según la hora o constante (diaria)
              --velocidad X         divide las esperas entre peticiones; solo para pruebas
                                    rápidas, porque distorsiona los tiempos de espera (1)
              --bots X              fracción de sesiones de bots, de 0 a 1 (0.02)
              --ayuda               muestra esta ayuda
            """;

    public static Configuracion porDefecto() {
        return new Configuracion(URI.create("http://localhost:7070"), Duration.ofHours(1), 60, 200, 42,
                ZoneId.systemDefault(), false, 1.0, 0.02);
    }

    /** Lee las opciones. Lanza IllegalArgumentException con un mensaje legible si hay errores. */
    public static Configuracion leer(String[] args) {
        Configuracion c = porDefecto();
        URI url = c.url;
        Duration duracion = c.duracion;
        double sesionesHora = c.sesionesHora;
        int usuarios = c.usuarios;
        long semilla = c.semilla;
        ZoneId zona = c.zona;
        boolean curvaPlana = c.curvaPlana;
        double velocidad = c.velocidad;
        double fraccionBots = c.fraccionBots;

        for (int i = 0; i < args.length; i++) {
            String opcion = args[i];
            if (i + 1 >= args.length) {
                throw new IllegalArgumentException("Falta el valor de " + opcion);
            }
            String valor = args[++i];
            switch (opcion) {
                case "--url" -> url = URI.create(valor.replaceAll("/+$", ""));
                case "--duracion" -> duracion = leerDuracion(valor);
                case "--sesiones-hora" -> sesionesHora = positivo(opcion, Double.parseDouble(valor));
                case "--usuarios" -> usuarios = (int) Math.max(3, positivo(opcion, Integer.parseInt(valor)));
                case "--semilla" -> semilla = Long.parseLong(valor);
                case "--zona" -> zona = ZoneId.of(valor);
                case "--curva" -> curvaPlana = switch (valor) {
                    case "diaria" -> false;
                    case "plana" -> true;
                    default -> throw new IllegalArgumentException("--curva debe ser diaria o plana, no " + valor);
                };
                case "--velocidad" -> velocidad = positivo(opcion, Double.parseDouble(valor));
                case "--bots" -> {
                    fraccionBots = Double.parseDouble(valor);
                    if (fraccionBots < 0 || fraccionBots > 1) {
                        throw new IllegalArgumentException("--bots debe estar entre 0 y 1");
                    }
                }
                default -> throw new IllegalArgumentException("Opción desconocida: " + opcion);
            }
        }
        return new Configuracion(url, duracion, sesionesHora, usuarios, semilla, zona, curvaPlana, velocidad,
                fraccionBots);
    }

    /** Duraciones como 90s, 30m, 2h o 1d. */
    static Duration leerDuracion(String texto) {
        String t = texto.strip().toLowerCase(Locale.ROOT);
        if (t.length() < 2) {
            throw new IllegalArgumentException("Duración no válida: " + texto + " (ejemplos: 90s, 30m, 2h, 1d)");
        }
        long n;
        try {
            n = Long.parseLong(t.substring(0, t.length() - 1));
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Duración no válida: " + texto + " (ejemplos: 90s, 30m, 2h, 1d)");
        }
        if (n <= 0) {
            throw new IllegalArgumentException("La duración debe ser positiva: " + texto);
        }
        return switch (t.charAt(t.length() - 1)) {
            case 's' -> Duration.ofSeconds(n);
            case 'm' -> Duration.ofMinutes(n);
            case 'h' -> Duration.ofHours(n);
            case 'd' -> Duration.ofDays(n);
            default -> throw new IllegalArgumentException("Duración no válida: " + texto + " (ejemplos: 90s, 30m, 2h, 1d)");
        };
    }

    private static double positivo(String opcion, double valor) {
        if (!(valor > 0)) {
            throw new IllegalArgumentException(opcion + " debe ser positivo");
        }
        return valor;
    }
}
