-- Esquema de la API. Se ejecuta al arrancar cada réplica (spring.sql.init.mode).
-- CREATE TABLE IF NOT EXISTS no es seguro si dos réplicas arrancan a la vez contra una
-- base vacía: las dos pueden intentar crear la tabla y una falla. El cerrojo de
-- PostgreSQL (pg_advisory_xact_lock) hace que lo ejecuten de una en una.
--
-- saldo es NUMERIC sin escala fija: conserva la escala exacta de BigDecimal
-- (1000.00 + 100.50 = 1100.50, sin redondeos). id es TEXT: sin límite de longitud,
-- para no introducir un 400 nuevo.
DO $$
BEGIN
    PERFORM pg_advisory_xact_lock(7070);
    CREATE TABLE IF NOT EXISTS cuentas (
        id    TEXT PRIMARY KEY,
        saldo NUMERIC NOT NULL CHECK (saldo >= 0)
    );
END
$$;
