package com.example.bank.dto;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.Digits;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Past;
import jakarta.validation.constraints.PositiveOrZero;
import jakarta.validation.constraints.Size;
import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@JsonIgnoreProperties({"secret"})
public class CreateAccountRequest extends BaseRequest {
    @NotBlank
    @Size(min = 3, max = 40)
    @JsonProperty("holder_name")
    private String holderName;

    @Email
    private String email;

    private String phone;

    @JsonIgnore
    private String internalNote;

    private String secret;

    private AccountType type;

    @Min(18)
    @Max(120)
    private int age;

    @PositiveOrZero
    private BigDecimal initialDeposit;

    @Digits(integer = 10, fraction = 0)
    private String nationalId;

    @Past
    private LocalDate birthDate;

    private Instant createdAt;

    private boolean vip;

    private Long referrerId;

    @NotEmpty
    private List<Beneficiary> beneficiaries;

    private Map<String, Integer> limits;

    private Branch branch;

    private transient String cache;

    @JsonProperty("phone_number")
    public String getPhone() {
        return phone;
    }
}
