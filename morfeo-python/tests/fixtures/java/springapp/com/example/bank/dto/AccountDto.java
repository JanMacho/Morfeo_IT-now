package com.example.bank.dto;

import java.math.BigDecimal;

public class AccountDto {
    private long id;
    private String holder;
    private BigDecimal balance;
    private AccountStatus status;

    public long getId() { return id; }
    public String getHolder() { return holder; }
    public BigDecimal getBalance() { return balance; }
    public AccountStatus getStatus() { return status; }
}
