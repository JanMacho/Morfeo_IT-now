package com.example.bank.web;

import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;

/** Clase abstracta sin implementacion en el jar. */
public abstract class AbstractAuditController {
    @GetMapping("/audit/log")
    public abstract List<String> log();
}
