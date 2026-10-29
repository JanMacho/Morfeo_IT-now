package com.example.bank.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;

/** Interfaz generica: la clase concreta recibe un metodo puente con el descriptor borrado. */
public interface CrudApi<T, ID> {
    @PutMapping("/{id}")
    ResponseEntity<T> replace(@PathVariable("id") ID id, @RequestBody T body);
}
