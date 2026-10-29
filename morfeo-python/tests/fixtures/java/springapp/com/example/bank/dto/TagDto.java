package com.example.bank.dto;

import jakarta.validation.constraints.Negative;
import jakarta.validation.constraints.NegativeOrZero;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.Set;

public class TagDto {
    @Size(max = 4)
    private String label;

    @Positive
    private Integer weight;

    @Negative
    private int penalty;

    @NegativeOrZero
    private long offset;

    private Set<String> aliases;

    private String[] codes;
}
