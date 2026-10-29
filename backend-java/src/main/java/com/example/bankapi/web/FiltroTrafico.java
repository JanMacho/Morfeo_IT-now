package com.example.bankapi.web;

import com.example.bankapi.logging.TrafficLogger;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;
import org.springframework.web.util.ContentCachingRequestWrapper;

import java.io.IOException;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;

/**
 * Filtro que envuelve CADA petición (éxito, error de negocio, ruta inexistente o
 * excepción): asigna la sesión por cookie, mide el tiempo y escribe la línea en
 * trafico.json. Va el primero de la cadena para que el tiempo incluya todo Spring MVC.
 *
 * Las peticiones a /actuator (healthcheck de Docker) no se registran: no son tráfico
 * de usuarios y ensuciarían el modelo de MORFEO.
 */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class FiltroTrafico extends OncePerRequestFilter {

    // Suficiente para los cuerpos de esta API; no se guarda más en memoria por petición
    private static final int LIMITE_CUERPO_BYTES = 16 * 1024;

    private final TrafficLogger trafficLogger;
    private final SesionPorCookie sesiones;
    private final ObjectMapper mapper = new ObjectMapper();

    public FiltroTrafico(TrafficLogger trafficLogger,
                         @Value("${trafico.sesion.inactividad-min}") long inactividadMin) {
        this.trafficLogger = trafficLogger;
        this.sesiones = new SesionPorCookie(Duration.ofMinutes(inactividadMin));
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return request.getRequestURI().startsWith("/actuator");
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        long inicio = System.nanoTime();
        String sesion = sesiones.resolver(request, response);
        ContentCachingRequestWrapper peticion = new ContentCachingRequestWrapper(request, LIMITE_CUERPO_BYTES);

        // Si una excepción llegara a escapar de Spring MVC, el contenedor respondería 500
        int statusSiFalla = HttpServletResponse.SC_INTERNAL_SERVER_ERROR;
        boolean ok = false;
        try {
            chain.doFilter(peticion, response);
            ok = true;
        } finally {
            double tiempoMs = (System.nanoTime() - inicio) / 1_000_000.0;
            trafficLogger.log(TrafficLogger.formatear(Instant.now(), request.getMethod(), request.getRequestURI(),
                    ok ? response.getStatus() : statusSiFalla, tiempoMs, idCliente(peticion), sesion));
        }
    }

    /**
     * Id de cliente para el log, en orden de prioridad:
     *   1. Variable de ruta {id} (GET /api/saldo/{id}).
     *   2. Campo "id" del cuerpo JSON (POST), si el handler llegó a leerlo.
     *   3. null (se escribe UNKNOWN), p.ej. en rutas inexistentes.
     */
    private String idCliente(ContentCachingRequestWrapper peticion) {
        Object variables = peticion.getAttribute(HandlerMapping.URI_TEMPLATE_VARIABLES_ATTRIBUTE);
        if (variables instanceof Map<?, ?> mapa && mapa.get("id") instanceof String id && !id.isBlank()) {
            return id;
        }

        byte[] cuerpo = peticion.getContentAsByteArray();
        if (cuerpo.length > 0) {
            try {
                JsonNode node = mapper.readTree(cuerpo);
                if (node != null && node.hasNonNull("id")) {
                    return node.get("id").asText();
                }
            } catch (IOException ignored) {
                // Cuerpo que no es JSON válido
            }
        }
        return null;
    }
}
