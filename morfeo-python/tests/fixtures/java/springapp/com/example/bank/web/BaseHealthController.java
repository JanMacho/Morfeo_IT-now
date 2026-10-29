package com.example.bank.web;

import org.springframework.web.bind.annotation.GetMapping;

/** Superclase abstracta con el mapping; lo hereda HealthController. */
public abstract class BaseHealthController {
    @GetMapping("/health")
    public String health() {
        return "UP";
    }
}
