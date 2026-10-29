package com.example.bank.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public enum AccountStatus {
    @JsonProperty("active") ACTIVE,
    @JsonProperty("blocked") BLOCKED,
    CLOSED
}
