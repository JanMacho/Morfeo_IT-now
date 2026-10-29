package com.example.bankapi.web;

import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;

import java.time.Duration;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * Identificador de sesión para el log de tráfico, guardado en una cookie.
 *
 * - Si la petición no trae una cookie válida, se genera un id nuevo (sesión nueva).
 * - En cada respuesta se reenvía la cookie con Max-Age = inactividad, así que caduca
 *   tras ese tiempo sin peticiones (30 min por defecto, el corte habitual en analítica
 *   web y el mismo que usa el log_analyzer cuando no hay session_id).
 *
 * El servidor no guarda nada: la caducidad la gestiona el cliente. Por eso no hay
 * coste de memoria aunque JMeter abra millones de sesiones, a diferencia de HttpSession.
 * Para que JMeter mantenga la sesión entre peticiones necesita un HTTP Cookie Manager;
 * sin él, cada petición es una sesión distinta.
 *
 * Es una cookie de analítica, no de autenticación: no da acceso a nada.
 */
public class SesionPorCookie {

    public static final String COOKIE = "MORFEO_SID";

    // Solo se aceptan ids con la forma de los que genera el servidor. Cualquier otro
    // valor (manipulado, con '|' o saltos de línea) se sustituye por uno nuevo.
    private static final Pattern ID_VALIDO = Pattern.compile("[0-9a-f]{32}");

    private final Duration inactividad;

    public SesionPorCookie(Duration inactividad) {
        this.inactividad = inactividad;
    }

    /**
     * Devuelve el id de sesión de la petición, o uno nuevo, y añade a la respuesta la
     * cookie renovada. Debe llamarse antes de que la respuesta se envíe.
     */
    public String resolver(HttpServletRequest request, HttpServletResponse response) {
        String id = leer(request);
        if (id == null) {
            id = UUID.randomUUID().toString().replace("-", "");
        }
        ResponseCookie cookie = ResponseCookie.from(COOKIE, id)
                .path("/")
                .httpOnly(true)
                .sameSite("Lax")
                .maxAge(inactividad)
                .build();
        response.addHeader(HttpHeaders.SET_COOKIE, cookie.toString());
        return id;
    }

    private static String leer(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return null;
        }
        for (Cookie cookie : cookies) {
            if (COOKIE.equals(cookie.getName()) && cookie.getValue() != null
                    && ID_VALIDO.matcher(cookie.getValue()).matches()) {
                return cookie.getValue();
            }
        }
        return null;
    }
}
