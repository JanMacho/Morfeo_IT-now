package com.example.bank.api;

import com.example.bank.dto.CardDto;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;

/** Interfaz al estilo de OpenAPI generator: anotaciones en la interfaz y metodos default. */
@RequestMapping("${openapi.bank.base-path:/v2}")
public interface CardsApi {

    @GetMapping(value = "/cards/{cardId}", produces = {"application/json"})
    default ResponseEntity<CardDto> getCard(@PathVariable("cardId") String cardId) {
        return ResponseEntity.ok(null);
    }

    @PostMapping(value = "/cards", consumes = {"application/json"}, produces = {"application/json"})
    default ResponseEntity<CardDto> addCard(@RequestBody CardDto cardDto) {
        return ResponseEntity.ok(cardDto);
    }

    @DeleteMapping("/cards/{cardId}")
    ResponseEntity<Void> deleteCard(@PathVariable("cardId") String cardId);
}
