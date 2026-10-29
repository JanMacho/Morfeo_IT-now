package com.example.bank.dto;

import java.math.BigDecimal;
import java.time.LocalDateTime;

public record Movement(LocalDateTime at, BigDecimal amount) {
}
