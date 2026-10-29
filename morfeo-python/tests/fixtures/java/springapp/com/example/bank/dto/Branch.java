package com.example.bank.dto;

/** Referencia ciclica: parent es otra Branch. */
public class Branch {
    private String code;
    private Branch parent;
}
