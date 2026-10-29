package com.example.bank.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** Varias rutas de clase y de metodo: producto cartesiano. */
@RestController
@RequestMapping({"/legacy", "/old/"})
public class LegacyController {
    @GetMapping({"a", "/b/"})
    public String any() {
        return "legacy";
    }
}
