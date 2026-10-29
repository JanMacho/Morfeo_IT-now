package com.example.bankapi.repository;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

/**
 * Cuentas en PostgreSQL (tabla cuentas, ver schema.sql).
 *
 * Cada operación es UNA sentencia SQL atómica: la comprobación y la escritura del
 * saldo ocurren dentro de la misma sentencia, y PostgreSQL bloquea la fila mientras
 * tanto. Así la atomicidad se mantiene aunque haya varias réplicas de la aplicación
 * contra la misma base de datos, cosa que un synchronized en memoria no podía dar.
 *
 * El saldo que se devuelve es el que escribe la propia sentencia (RETURNING), no una
 * lectura posterior que podría ver ya el efecto de otra petición.
 */
@Repository
public class BancoRepository {

    /** Resultado de una retirada. saldo es el nuevo (HECHA) o el actual (SIN_FONDOS). */
    public record Retirada(Estado estado, BigDecimal saldo) {
        public enum Estado { HECHA, SIN_FONDOS, NO_EXISTE }
    }

    private final JdbcTemplate jdbc;

    public BancoRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** Saldo actual, o vacío si la cuenta no existe. */
    public Optional<BigDecimal> saldo(String id) {
        return unico(jdbc.queryForList("SELECT saldo FROM cuentas WHERE id = ?", BigDecimal.class, id));
    }

    /** Suma la cantidad y devuelve el saldo nuevo, o vacío si la cuenta no existe. */
    public Optional<BigDecimal> ingresar(String id, BigDecimal cantidad) {
        return unico(jdbc.queryForList(
                "UPDATE cuentas SET saldo = saldo + ? WHERE id = ? RETURNING saldo",
                BigDecimal.class, cantidad, id));
    }

    /**
     * Resta la cantidad solo si hay fondos. Si la sentencia no toca ninguna fila, una
     * consulta aparte distingue entre cuenta inexistente y fondos insuficientes.
     */
    public Retirada retirar(String id, BigDecimal cantidad) {
        Optional<BigDecimal> nuevo = unico(jdbc.queryForList(
                "UPDATE cuentas SET saldo = saldo - ? WHERE id = ? AND saldo >= ? RETURNING saldo",
                BigDecimal.class, cantidad, id, cantidad));
        if (nuevo.isPresent()) {
            return new Retirada(Retirada.Estado.HECHA, nuevo.get());
        }
        return saldo(id)
                .map(actual -> new Retirada(Retirada.Estado.SIN_FONDOS, actual))
                .orElseGet(() -> new Retirada(Retirada.Estado.NO_EXISTE, null));
    }

    /**
     * Crea la cuenta de forma atómica: si dos peticiones intentan crear el mismo id a
     * la vez, solo una inserta la fila.
     *
     * @return true si la cuenta se creó, false si el id ya existía
     */
    public boolean crear(String id, BigDecimal saldoInicial) {
        return jdbc.update("INSERT INTO cuentas (id, saldo) VALUES (?, ?) ON CONFLICT (id) DO NOTHING",
                id, saldoInicial) == 1;
    }

    private static Optional<BigDecimal> unico(List<BigDecimal> filas) {
        return filas.isEmpty() ? Optional.empty() : Optional.of(filas.get(0));
    }
}
