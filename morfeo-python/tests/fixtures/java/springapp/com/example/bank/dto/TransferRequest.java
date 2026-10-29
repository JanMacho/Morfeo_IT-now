package com.example.bank.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Optional;
import java.util.UUID;

/** Record: @JsonProperty en el componente (javac lo copia al campo y al accesor). */
public record TransferRequest(
        @JsonProperty("from_account") @NotBlank String fromAccount,
        @JsonProperty("to_account") @NotBlank String toAccount,
        @NotNull @DecimalMin("0.01") @Digits(integer = 10, fraction = 2) BigDecimal amount,
        @Pattern(regexp = "[A-Z]{3}") String currency,
        @javax.validation.constraints.NotNull LocalDate valueDate,
        UUID reference,
        Optional<String> concept) {
}
