package com.example.bank.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.NotBlank;

public class Beneficiary {
    @NotBlank
    private String name;

    @Max(100)
    private int share;
}
