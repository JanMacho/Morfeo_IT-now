-- Vuelve la base de datos al estado inicial: borra todas las cuentas y carga los 3
-- clientes de siempre. Lo ejecuta una persona antes de cada experimento; la app no
-- lo hace al arrancar, porque con varias réplicas cada pod que arrancara borraría
-- los datos de todos.
--
--   docker compose exec -T postgres psql -U banco -d banco < db/reset.sql
TRUNCATE cuentas;
INSERT INTO cuentas (id, saldo) VALUES
    ('user_001', 1000.00),
    ('user_002', 500.00),
    ('user_003', 2500.00);
