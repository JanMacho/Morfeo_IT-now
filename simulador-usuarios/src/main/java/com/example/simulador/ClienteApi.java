package com.example.simulador;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;

/**
 * Cliente HTTP compartido por todas las sesiones, con contadores de respuestas.
 *
 * La cookie de sesión (MORFEO_SID) la guarda cada sesión en su Cookie: así un único
 * HttpClient sirve para miles de sesiones a la vez, cada una con su propia cookie,
 * igual que el HTTP Cookie Manager de JMeter por hilo.
 */
public class ClienteApi {

    public static final String COOKIE_SESION = "MORFEO_SID";

    /** Cookie de una sesión. Solo la usa el hilo de esa sesión. */
    public static final class Cookie {
        private String valor;

        public String valor() {
            return valor;
        }
    }

    /** Respuesta reducida. status = -1 si no hubo respuesta (servidor caído, timeout...). */
    public record Respuesta(int status, String cuerpo) {
        public boolean ok() {
            return status >= 200 && status < 300;
        }
    }

    private final HttpClient http;
    private final URI base;
    private final Map<Integer, LongAdder> porStatus = new ConcurrentHashMap<>();
    private final LongAdder peticiones = new LongAdder();

    public ClienteApi(URI base) {
        this.base = base;
        this.http = HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(5))
                .build();
    }

    public Respuesta get(String ruta, Cookie cookie) {
        return enviar(peticion(ruta, cookie).GET().build(), cookie);
    }

    public Respuesta post(String ruta, String json, Cookie cookie) {
        HttpRequest req = peticion(ruta, cookie)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(json))
                .build();
        return enviar(req, cookie);
    }

    private HttpRequest.Builder peticion(String ruta, Cookie cookie) {
        HttpRequest.Builder b = HttpRequest.newBuilder(base.resolve(ruta)).timeout(Duration.ofSeconds(30));
        if (cookie != null && cookie.valor != null) {
            b.header("Cookie", COOKIE_SESION + "=" + cookie.valor);
        }
        return b;
    }

    private Respuesta enviar(HttpRequest req, Cookie cookie) {
        peticiones.increment();
        int status;
        String cuerpo = "";
        try {
            HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString());
            status = r.statusCode();
            cuerpo = r.body();
            if (cookie != null) {
                r.headers().allValues("Set-Cookie").stream()
                        .map(ClienteApi::valorCookieSesion)
                        .filter(v -> v != null)
                        .findFirst()
                        .ifPresent(v -> cookie.valor = v);
            }
        } catch (IOException e) {
            status = -1;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            status = -1;
        }
        porStatus.computeIfAbsent(status, k -> new LongAdder()).increment();
        return new Respuesta(status, cuerpo);
    }

    /** Valor de MORFEO_SID en una cabecera Set-Cookie, o null si es otra cookie. */
    static String valorCookieSesion(String setCookie) {
        String prefijo = COOKIE_SESION + "=";
        if (!setCookie.startsWith(prefijo)) {
            return null;
        }
        int fin = setCookie.indexOf(';');
        return setCookie.substring(prefijo.length(), fin < 0 ? setCookie.length() : fin);
    }

    /** Comprueba que la API responde (healthcheck, que no se registra en trafico.json). */
    public boolean disponible() {
        try {
            HttpRequest req = HttpRequest.newBuilder(base.resolve("/actuator/health"))
                    .timeout(Duration.ofSeconds(5)).GET().build();
            return http.send(req, HttpResponse.BodyHandlers.discarding()).statusCode() == 200;
        } catch (IOException e) {
            return false;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    public long peticiones() {
        return peticiones.sum();
    }

    /** Respuestas por código HTTP (-1 = sin respuesta), ordenadas por código. */
    public Map<Integer, Long> respuestasPorStatus() {
        Map<Integer, Long> copia = new TreeMap<>();
        porStatus.forEach((k, v) -> copia.put(k, v.sum()));
        return copia;
    }
}
