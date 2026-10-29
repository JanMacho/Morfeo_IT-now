package com.example.bankapi.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.math.BigDecimal;

/**
 * DTO para /api/usuario/crear: {"id": "...", "saldo_inicial": X}.
 * El campo JSON en snake_case "saldo_inicial" se mapea al atributo Java "saldoInicial"
 * mediante @JsonProperty.
 */
public class CrearUsuarioRequest {

    private String id;

    @JsonProperty("saldo_inicial")
    private BigDecimal saldoInicial;

    public CrearUsuarioRequest() {
    }

    public String getId() {
        return id;
    }

    public void setId(String id) {
        this.id = id;
    }

    public BigDecimal getSaldoInicial() {
        return saldoInicial;
    }

    public void setSaldoInicial(BigDecimal saldoInicial) {
        this.saldoInicial = saldoInicial;
    }
}
