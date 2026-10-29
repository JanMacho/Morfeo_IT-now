package com.example.bankapi;

import com.example.bankapi.logging.TrafficLogger;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;

import java.nio.file.Path;

/**
 * API REST bancaria pensada como "diana" de pruebas de estrés con JMeter y como
 * aplicación de referencia de MORFEO.
 *
 * - Persistencia 100% en memoria (ConcurrentHashMap), sin base de datos externa.
 * - Dinero representado con BigDecimal (nunca double) para precisión financiera exacta.
 * - Retiradas atómicas y thread-safe (ver Cuenta.retirar()).
 * - Un filtro (FiltroTrafico) mide cada petición, le asigna una sesión por cookie y
 *   escribe una traza en trafico.json de forma asíncrona (ver TrafficLogger).
 *
 * Configuración: application.properties, sobrescribible con variables de entorno
 * (PORT, TRAFFIC_LOG, MAX_THREADS, SESSION_TIMEOUT_MIN).
 */
@SpringBootApplication
public class BankApiApplication {

    public static void main(String[] args) {
        SpringApplication.run(BankApiApplication.class, args);
    }

    /**
     * Logger de tráfico. Spring llama a shutdown() al cerrar el contexto, después de
     * parar el servidor, así que las últimas peticiones también llegan al fichero.
     */
    @Bean(destroyMethod = "shutdown")
    public TrafficLogger trafficLogger(@Value("${trafico.log.path}") String ruta) {
        return new TrafficLogger(Path.of(ruta));
    }
}
