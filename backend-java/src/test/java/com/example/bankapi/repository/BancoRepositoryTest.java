package com.example.bankapi.repository;

import com.example.bankapi.PostgresCompartido;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.JdbcTemplate;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Atomicidad de las operaciones contra un PostgreSQL real. Sustituye al antiguo
 * CuentaTest: la garantía ya no la da un synchronized sino cada sentencia SQL, y por
 * eso sirve también con varias réplicas.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.NONE,
        properties = "trafico.log.path=${java.io.tmpdir}/bank-api-repositorio-test.log")
class BancoRepositoryTest extends PostgresCompartido {

    @Autowired
    BancoRepository repositorio;

    @Autowired
    JdbcTemplate jdbc;

    @BeforeEach
    void datosIniciales() {
        resetear(jdbc);
    }

    /** Lanza las tareas a la vez desde muchos hilos y devuelve sus resultados. */
    private static <T> List<T> aLaVez(int hilos, List<Callable<T>> tareas) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(hilos);
        CountDownLatch salida = new CountDownLatch(1);
        List<Future<T>> futuros = new ArrayList<>();
        for (Callable<T> tarea : tareas) {
            futuros.add(pool.submit(() -> {
                salida.await();
                return tarea.call();
            }));
        }
        salida.countDown();
        List<T> resultados = new ArrayList<>();
        for (Future<T> f : futuros) {
            resultados.add(f.get());
        }
        pool.shutdown();
        return resultados;
    }

    @Test
    void conservaLaEscalaExactaDeBigDecimal() {
        assertEquals(new BigDecimal("1000.00"), repositorio.saldo("user_001").orElseThrow());
        assertEquals(new BigDecimal("1100.50"), repositorio.ingresar("user_001", new BigDecimal("100.50")).orElseThrow());
        assertEquals(new BigDecimal("1050.25"),
                repositorio.retirar("user_001", new BigDecimal("50.25")).saldo());
    }

    @Test
    void distingueCuentaInexistenteDeFondosInsuficientes() {
        assertEquals(BancoRepository.Retirada.Estado.NO_EXISTE,
                repositorio.retirar("nadie", BigDecimal.ONE).estado());
        BancoRepository.Retirada sinFondos = repositorio.retirar("user_002", new BigDecimal("500.01"));
        assertEquals(BancoRepository.Retirada.Estado.SIN_FONDOS, sinFondos.estado());
        assertEquals(new BigDecimal("500.00"), sinFondos.saldo());
        assertTrue(repositorio.ingresar("nadie", BigDecimal.ONE).isEmpty());
        assertTrue(repositorio.saldo("nadie").isEmpty());
    }

    @Test
    void retiradasConcurrentesSobreLaMismaCuentaSonAtomicas() throws Exception {
        // 500 € y 2.000 retiradas de 1 € lanzadas a la vez: deben salir exactamente 500
        List<Callable<BancoRepository.Retirada.Estado>> tareas = new ArrayList<>();
        for (int i = 0; i < 2_000; i++) {
            tareas.add(() -> repositorio.retirar("user_002", BigDecimal.ONE).estado());
        }
        List<BancoRepository.Retirada.Estado> estados = aLaVez(64, tareas);

        assertEquals(500, estados.stream().filter(e -> e == BancoRepository.Retirada.Estado.HECHA).count());
        assertEquals(1_500, estados.stream().filter(e -> e == BancoRepository.Retirada.Estado.SIN_FONDOS).count());
        assertEquals(0, repositorio.saldo("user_002").orElseThrow().compareTo(BigDecimal.ZERO));
    }

    @Test
    void ingresosYRetiradasMezcladosCuadran() throws Exception {
        // El saldo nunca baja de 900, así que ninguna retirada falla y el total vuelve a 1000.00
        List<Callable<Boolean>> tareas = new ArrayList<>();
        for (int i = 0; i < 1_000; i++) {
            tareas.add(() -> repositorio.ingresar("user_001", new BigDecimal("0.10")).isPresent());
            tareas.add(() -> repositorio.retirar("user_001", new BigDecimal("0.10")).estado()
                    == BancoRepository.Retirada.Estado.HECHA);
        }
        assertTrue(aLaVez(64, tareas).stream().allMatch(ok -> ok));
        assertEquals(new BigDecimal("1000.00"), repositorio.saldo("user_001").orElseThrow());
    }

    @Test
    void soloUnaDeVariasAltasSimultaneasDelMismoIdTieneExito() throws Exception {
        List<Callable<Boolean>> tareas = new ArrayList<>();
        for (int i = 0; i < 50; i++) {
            tareas.add(() -> repositorio.crear("user_nuevo", new BigDecimal("10")));
        }
        assertEquals(1, aLaVez(50, tareas).stream().filter(creado -> creado).count());
        assertFalse(repositorio.crear("user_001", BigDecimal.ONE));
        assertEquals(new BigDecimal("1000.00"), repositorio.saldo("user_001").orElseThrow());
    }

    @Test
    void elSaldoNuncaPuedeQuedarNegativo() {
        // La restricción CHECK de la tabla es la última barrera
        jdbc.update("UPDATE cuentas SET saldo = 0 WHERE id = 'user_003'");
        assertEquals(BancoRepository.Retirada.Estado.SIN_FONDOS,
                repositorio.retirar("user_003", new BigDecimal("0.01")).estado());
    }
}
