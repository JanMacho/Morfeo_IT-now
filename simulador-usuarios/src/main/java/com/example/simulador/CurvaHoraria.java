package com.example.simulador;

/**
 * Peso de cada hora del día (hora local) sobre la hora punta: casi nada de madrugada,
 * un pico por la mañana (10 h) y otro por la tarde (18-19 h), como la banca online.
 */
public final class CurvaHoraria {

    private static final double[] DIARIA = {
            0.05, 0.03, 0.02, 0.02, 0.02, 0.04,   // 0-5 h
            0.10, 0.30, 0.65, 0.95, 1.00, 0.90,   // 6-11 h
            0.75, 0.70, 0.60, 0.55, 0.65, 0.85,   // 12-17 h
            0.95, 0.80, 0.55, 0.35, 0.20, 0.10    // 18-23 h
    };

    private final boolean plana;

    public CurvaHoraria(boolean plana) {
        this.plana = plana;
    }

    /** Peso entre 0 y 1 de la hora local indicada (0-23). */
    public double peso(int hora) {
        return plana ? 1.0 : DIARIA[hora];
    }
}
