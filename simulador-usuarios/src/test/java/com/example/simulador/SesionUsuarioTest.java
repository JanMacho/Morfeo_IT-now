package com.example.simulador;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.SplittableRandom;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Prueba las sesiones contra un servidor falso que imita a la API: da una cookie
 * MORFEO_SID nueva a quien no la trae y apunta cada petición que recibe.
 */
class SesionUsuarioTest {

    record Peticion(String metodo, String ruta, String cookie, String cuerpo) {
    }

    private static final double MUY_RAPIDO = 1_000_000; // sin esperas reales

    private HttpServer servidor;
    private final List<Peticion> recibidas = new CopyOnWriteArrayList<>();
    private final AtomicInteger contadorSesiones = new AtomicInteger();
    private ClienteApi api;

    @BeforeEach
    void arrancar() throws IOException {
        servidor = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        servidor.createContext("/", this::responder);
        servidor.start();
        api = new ClienteApi(URI.create("http://localhost:" + servidor.getAddress().getPort()));
    }

    @AfterEach
    void parar() {
        servidor.stop(0);
    }

    private void responder(HttpExchange ex) throws IOException {
        String cookie = ex.getRequestHeaders().getFirst("Cookie");
        String cuerpo = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String ruta = ex.getRequestURI().getPath();
        recibidas.add(new Peticion(ex.getRequestMethod(), ruta, cookie, cuerpo));

        String sid = cookie != null ? cookie.substring(cookie.indexOf('=') + 1) : "sid" + contadorSesiones.incrementAndGet();
        ex.getResponseHeaders().add("Set-Cookie", "MORFEO_SID=" + sid + "; Path=/; Max-Age=1800; HttpOnly");

        int status = 200;
        String respuesta = "{\"id\":\"x\",\"saldo\":500.00}";
        if (ruta.equals("/api/usuario/crear")) {
            status = 201;
        } else if (!ruta.startsWith("/api/saldo/") && !ruta.equals("/api/ingreso") && !ruta.equals("/api/retirada")) {
            status = 404;
            respuesta = "{\"error\":\"Ruta no encontrada\"}";
        }
        byte[] bytes = respuesta.getBytes(StandardCharsets.UTF_8);
        ex.sendResponseHeaders(status, bytes.length);
        ex.getResponseBody().write(bytes);
        ex.close();
    }

    private void sesion(Poblacion.Usuario usuario, boolean bot, long semilla) {
        new SesionUsuario(api, usuario, bot, new SplittableRandom(semilla), MUY_RAPIDO).run();
    }

    @Test
    void todasLasPeticionesDeUnaSesionLlevanSuCookie() {
        Poblacion.Usuario usuario = new Poblacion(3).elegir(new SplittableRandom(1));
        for (int s = 0; s < 20; s++) {
            sesion(usuario, false, s);
        }

        // La primera petición de cada sesión llega sin cookie; las demás, con la de su sesión
        long sinCookie = recibidas.stream().filter(p -> p.cookie() == null).count();
        assertEquals(20, sinCookie);
        Set<String> sesionesVistas = recibidas.stream().filter(p -> p.cookie() != null)
                .map(Peticion::cookie).collect(Collectors.toSet());
        assertTrue(sesionesVistas.size() <= 20);
        assertTrue(recibidas.size() > 20, "las sesiones deben tener varias peticiones: " + recibidas.size());
    }

    @Test
    void unClienteNuevoEmpiezaDandoseDeAltaYSoloUnaVez() {
        Poblacion p = new Poblacion(50);
        Poblacion.Usuario nuevo = null;
        SplittableRandom rng = new SplittableRandom(3);
        while (nuevo == null) {
            Poblacion.Usuario u = p.elegir(rng);
            if (!u.registrado()) {
                nuevo = u;
            }
        }
        sesion(nuevo, false, 1);
        sesion(nuevo, false, 2);

        Peticion primera = recibidas.get(0);
        assertEquals("/api/usuario/crear", primera.ruta());
        assertTrue(primera.cuerpo().contains("\"id\":\"" + nuevo.id() + "\""), primera.cuerpo());
        assertTrue(primera.cuerpo().contains("\"saldo_inicial\":"), primera.cuerpo());
        assertEquals(1, recibidas.stream().filter(r -> r.ruta().equals("/api/usuario/crear")).count());
        assertTrue(nuevo.registrado());
    }

    @Test
    void lasPeticionesSonLasDeLaApiConCuerposValidos() {
        Poblacion.Usuario usuario = new Poblacion(3).elegir(new SplittableRandom(1));
        for (int s = 0; s < 200; s++) {
            sesion(usuario, false, s);
        }
        Set<String> rutas = recibidas.stream().map(Peticion::ruta).collect(Collectors.toSet());
        assertTrue(Set.of("/api/saldo/" + usuario.id(), "/api/ingreso", "/api/retirada").containsAll(rutas), rutas.toString());

        Map<String, Long> porRuta = recibidas.stream().collect(Collectors.groupingBy(Peticion::ruta, Collectors.counting()));
        assertTrue(porRuta.get("/api/saldo/" + usuario.id()) > porRuta.get("/api/ingreso"), porRuta.toString());

        for (Peticion p : recibidas) {
            if (p.metodo().equals("POST") && !p.cuerpo().endsWith(":}")) {
                assertTrue(p.cuerpo().matches("\\{\"id\":\"user_\\d+\",\"cantidad\":\\d+\\.\\d{2}}"), p.cuerpo());
            }
        }
    }

    @Test
    void losBotsNoLlevanCookieNiTocanLaApi() {
        for (int s = 0; s < 20; s++) {
            sesion(null, true, s);
        }
        assertFalse(recibidas.isEmpty());
        for (Peticion p : recibidas) {
            assertNull(p.cookie());
            assertEquals("GET", p.metodo());
        }
        assertTrue(recibidas.stream().noneMatch(p -> p.ruta().startsWith("/api/saldo/") || p.ruta().equals("/api/ingreso")));
    }

    @Test
    void extraeLaCookieDeSesion() {
        assertEquals("abc", ClienteApi.valorCookieSesion("MORFEO_SID=abc; Path=/; Max-Age=1800"));
        assertEquals("abc", ClienteApi.valorCookieSesion("MORFEO_SID=abc"));
        assertNull(ClienteApi.valorCookieSesion("OTRA=abc; Path=/"));
    }
}
