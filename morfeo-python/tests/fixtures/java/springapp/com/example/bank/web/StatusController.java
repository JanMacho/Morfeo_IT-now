package com.example.bank.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Repite GET /health: se conserva el de HealthController. */
@RestController
public class StatusController {
    @GetMapping("/health")
    public String status() {
        return "UP";
    }
}
