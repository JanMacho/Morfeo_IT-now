package com.example.bank.dto;

import jakarta.validation.constraints.Future;
import jakarta.validation.constraints.Pattern;
import java.time.LocalTime;
import java.time.OffsetDateTime;

public class CardDto {
    @Pattern(regexp = "^\\d{16}$")
    private String number;

    @Future
    private OffsetDateTime expiresAt;

    private LocalTime dailyCutoff;

    private char kind;

    private double cashback;

    private float fee;

    private short pin;

    private Object metadata;
}
