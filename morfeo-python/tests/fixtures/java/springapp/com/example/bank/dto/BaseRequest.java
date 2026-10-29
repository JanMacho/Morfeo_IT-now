package com.example.bank.dto;

import jakarta.validation.constraints.NotNull;
import java.util.UUID;

/** Superclase: sus campos van primero en el esquema. */
public abstract class BaseRequest {
    @NotNull
    protected UUID requestId;

    protected static final String VERSION = "1";
}
