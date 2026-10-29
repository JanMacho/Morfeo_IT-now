package com.example.bank.service;

import com.example.bank.dto.AccountDto;
import java.util.ArrayList;
import java.util.List;

/** Clase que no es controlador: no debe producir endpoints. */
public class AccountService {
    private final List<AccountDto> accounts = new ArrayList<>();

    public List<AccountDto> findAll() {
        return accounts;
    }

    public AccountDto get(long id) {
        return accounts.isEmpty() ? null : accounts.get((int) id);
    }
}
