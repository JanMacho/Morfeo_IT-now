package com.example.bankapi.web;

import com.example.bankapi.dto.CrearUsuarioRequest;
import com.example.bankapi.dto.MovimientoRequest;
import com.example.bankapi.repository.BancoRepository;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Los cuatro endpoints de la API. Las rutas y los DTO forman parte del contrato con
 * el jar_scanner de MORFEO (endpoints.json): si se cambian, hay que avisar al bloque
 * de Python.
 *
 * Los errores de negocio se devuelven aquí con su código. Los de formato (JSON mal
 * formado, ruta o método inexistente), los de base de datos (503) y los inesperados
 * los resuelve ManejadorErrores.
 */
@RestController
public class BancoController {

    private final BancoRepository repositorio;

    public BancoController(BancoRepository repositorio) {
        this.repositorio = repositorio;
    }

    /** GET /api/saldo/{id} -> devuelve el saldo actual. 404 si no existe. */
    @GetMapping("/api/saldo/{id}")
    public ResponseEntity<Map<String, Object>> saldo(@PathVariable String id) {
        return repositorio.saldo(id)
                .map(saldo -> ResponseEntity.ok(respuestaSaldo(id, saldo)))
                .orElseGet(() -> error(HttpStatus.NOT_FOUND, "Cliente no encontrado", "id", id));
    }

    /** POST /api/ingreso -> {"id": "...", "cantidad": X} */
    @PostMapping("/api/ingreso")
    public ResponseEntity<Map<String, Object>> ingreso(@RequestBody MovimientoRequest req) {
        ResponseEntity<Map<String, Object>> invalida = validarMovimiento(req);
        if (invalida != null) {
            return invalida;
        }
        return repositorio.ingresar(req.getId(), req.getCantidad())
                .map(saldo -> ResponseEntity.ok(respuestaSaldo(req.getId(), saldo)))
                .orElseGet(() -> error(HttpStatus.NOT_FOUND, "Cliente no encontrado", "id", req.getId()));
    }

    /** POST /api/retirada -> {"id": "...", "cantidad": X}. 400 si no hay fondos. */
    @PostMapping("/api/retirada")
    public ResponseEntity<Map<String, Object>> retirada(@RequestBody MovimientoRequest req) {
        ResponseEntity<Map<String, Object>> invalida = validarMovimiento(req);
        if (invalida != null) {
            return invalida;
        }

        // Una sola sentencia SQL comprueba los fondos y descuenta (ver BancoRepository),
        // sin ventana para condiciones de carrera aunque haya varias réplicas.
        BancoRepository.Retirada resultado = repositorio.retirar(req.getId(), req.getCantidad());
        return switch (resultado.estado()) {
            case HECHA -> ResponseEntity.ok(respuestaSaldo(req.getId(), resultado.saldo()));
            case NO_EXISTE -> error(HttpStatus.NOT_FOUND, "Cliente no encontrado", "id", req.getId());
            case SIN_FONDOS -> {
                Map<String, Object> body = new LinkedHashMap<>();
                body.put("error", "Fondos insuficientes");
                body.put("id", req.getId());
                body.put("saldo_actual", resultado.saldo());
                yield ResponseEntity.badRequest().body(body);
            }
        };
    }

    /** POST /api/usuario/crear -> {"id": "...", "saldo_inicial": X} */
    @PostMapping("/api/usuario/crear")
    public ResponseEntity<Map<String, Object>> crearUsuario(@RequestBody CrearUsuarioRequest req) {
        if (req.getId() == null || req.getId().isBlank()) {
            return error(HttpStatus.BAD_REQUEST, "El campo 'id' es obligatorio");
        }
        if (req.getSaldoInicial() == null || req.getSaldoInicial().compareTo(BigDecimal.ZERO) < 0) {
            return error(HttpStatus.BAD_REQUEST, "El campo 'saldo_inicial' debe ser un número >= 0");
        }
        if (!repositorio.crear(req.getId(), req.getSaldoInicial())) {
            return error(HttpStatus.CONFLICT, "El usuario ya existe", "id", req.getId());
        }
        return ResponseEntity.status(HttpStatus.CREATED).body(respuestaSaldo(req.getId(), req.getSaldoInicial()));
    }

    /**
     * Validación común a /api/ingreso y /api/retirada: id presente y cantidad
     * positiva. Devuelve la respuesta de error, o null si la petición es válida.
     */
    private static ResponseEntity<Map<String, Object>> validarMovimiento(MovimientoRequest req) {
        if (req.getId() == null || req.getId().isBlank()) {
            return error(HttpStatus.BAD_REQUEST, "El campo 'id' es obligatorio");
        }
        if (req.getCantidad() == null || req.getCantidad().compareTo(BigDecimal.ZERO) <= 0) {
            return error(HttpStatus.BAD_REQUEST, "El campo 'cantidad' debe ser un número positivo");
        }
        return null;
    }

    private static Map<String, Object> respuestaSaldo(String id, BigDecimal saldo) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("id", id);
        body.put("saldo", saldo);
        return body;
    }

    private static ResponseEntity<Map<String, Object>> error(HttpStatus status, String mensaje) {
        return ResponseEntity.status(status).body(Map.of("error", mensaje));
    }

    private static ResponseEntity<Map<String, Object>> error(HttpStatus status, String mensaje, String clave, Object valor) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", mensaje);
        body.put(clave, valor);
        return ResponseEntity.status(status).body(body);
    }
}
