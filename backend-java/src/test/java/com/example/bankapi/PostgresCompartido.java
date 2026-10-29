package com.example.bankapi;

import org.springframework.boot.testcontainers.service.connection.ServiceConnection;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.testcontainers.containers.PostgreSQLContainer;

/**
 * Un único PostgreSQL 16 en Docker (Testcontainers) para todos los tests que lo
 * necesitan. Se arranca una vez por ejecución y Testcontainers lo elimina al acabar.
 * @ServiceConnection hace que Spring se conecte a él en lugar de a DB_URL.
 *
 * Los datos sobreviven a los reinicios del contexto de Spring, así que cada test
 * deja la tabla como al principio con resetear(), que ejecuta el mismo db/reset.sql
 * que se usa antes de cada experimento.
 */
public abstract class PostgresCompartido {

    @ServiceConnection
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16");

    static {
        POSTGRES.start();
    }

    protected static void resetear(JdbcTemplate jdbc) {
        new ResourceDatabasePopulator(new FileSystemResource("db/reset.sql")).execute(jdbc.getDataSource());
    }
}
