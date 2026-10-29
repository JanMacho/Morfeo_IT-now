package com.example.bankapi.dto;

import java.math.BigDecimal;

/**
 * DTO deserializado automáticamente por Jackson (vía ctx.bodyAsClass) a partir del
 * cuerpo JSON de /api/ingreso y /api/retirada: {"id": "...", "cantidad": X}.
 *
 * Se usa BigDecimal (no double/float) para representar dinero y evitar errores de
 * precisión de coma flotante en operaciones financieras.
 */
public class MovimientoRequest {

    private String id;
    private BigDecimal cantidad;

    public MovimientoRequest() {
        // Constructor vacío requerido por Jackson
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public BigDecimal getCantidad() {
        return cantidad;
    }

    public void setCantidad(BigDecimal cantidad) {
        this.cantidad = cantidad;
    }
}
