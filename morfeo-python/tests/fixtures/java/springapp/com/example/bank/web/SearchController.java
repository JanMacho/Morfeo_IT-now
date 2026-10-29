package com.example.bank.web;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Dos handlers sobrecargados con el mismo nombre y el mismo número de parámetros. */
@RestController
@RequestMapping("/search")
public class SearchController {

    @GetMapping("/by-id/{id}")
    public String find(@PathVariable Long id) {
        return "id";
    }

    @GetMapping("/by-name")
    public String find(@RequestParam String name) {
        return "name";
    }
}
