package com.example.bankapi.web;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.dao.TransientDataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.resource.NoResourceFoundException;

import jakarta.servlet.http.HttpServletRequest;

import java.util.Map;

/**
 * Respuestas de error uniformes ({"error": "..."}) para lo que no resuelven los
 * handlers. El código importa: el análisis de tráfico separa los errores del cliente
 * (4xx) de los fallos del servidor (5xx).
 *
 * - Cuerpo ausente, JSON mal formado o con tipos incorrectos -> 400.
 * - Errores propios de Spring MVC (ruta inexistente 404, método no permitido 405,
 *   Content-Type no soportado 415...) -> su código.
 * - Base de datos caída, o sin conexión libre en el pool a tiempo (DB_TIMEOUT_MS)
 *   -> 503. Es el síntoma de que la cola del DataSource está saturada, y se distingue
 *   del 500 porque no es un fallo de la aplicación.
 * - Cualquier otra excepción es un fallo nuestro -> 500. El detalle va a la consola,
 *   no a la respuesta.
 */
@RestControllerAdvice
public class ManejadorErrores {

    private static final Logger LOG = LoggerFactory.getLogger(ManejadorErrores.class);

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Map<String, Object>> cuerpoInvalido() {
        return respuesta(HttpStatus.BAD_REQUEST, "Cuerpo JSON ausente, inválido o con tipos incorrectos");
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<Map<String, Object>> rutaInexistente() {
        return respuesta(HttpStatus.NOT_FOUND, "Ruta no encontrada");
    }

    /**
     * DataAccessResourceFailureException incluye CannotGetJdbcConnectionException (pool
     * agotado o BD inaccesible) y las conexiones cortadas a mitad de consulta.
     * TransientDataAccessException cubre los fallos temporales (timeouts de consulta...).
     */
    @ExceptionHandler({DataAccessResourceFailureException.class, TransientDataAccessException.class})
    public ResponseEntity<Map<String, Object>> baseDeDatosNoDisponible(Exception e, HttpServletRequest request) {
        LOG.warn("Base de datos no disponible en {} {}: {}", request.getMethod(), request.getRequestURI(), e.getMessage());
        return respuesta(HttpStatus.SERVICE_UNAVAILABLE, "Base de datos no disponible");
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, Object>> otro(Exception e, HttpServletRequest request) {
        if (e instanceof ErrorResponse errorSpring) {
            HttpStatusCode status = errorSpring.getStatusCode();
            HttpStatus conocido = HttpStatus.resolve(status.value());
            return respuesta(status, conocido != null ? conocido.getReasonPhrase() : "Error");
        }
        LOG.error("Error no controlado en {} {}", request.getMethod(), request.getRequestURI(), e);
        return respuesta(HttpStatus.INTERNAL_SERVER_ERROR, "Error interno del servidor");
    }

    private static ResponseEntity<Map<String, Object>> respuesta(HttpStatusCode status, String mensaje) {
        return ResponseEntity.status(status).body(Map.of("error", mensaje));
    }
}
