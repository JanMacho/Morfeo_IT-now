package com.example.bank.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;

/** Interfaz sin implementacion en el jar. */
public interface ReportsApi {
    @GetMapping("/reports/daily")
    ResponseEntity<String> daily(@RequestParam("date") String date);
}
