-- Clientes precargados. Se ejecuta al arrancar cada réplica: ON CONFLICT DO NOTHING
-- no toca los que ya existen, así que no borra datos ni falla con varias réplicas.
-- simulador-usuarios da por hecho que existen exactamente estos 3.
INSERT INTO cuentas (id, saldo) VALUES
    ('user_001', 1000.00),
    ('user_002', 500.00),
    ('user_003', 2500.00)
ON CONFLICT (id) DO NOTHING;
