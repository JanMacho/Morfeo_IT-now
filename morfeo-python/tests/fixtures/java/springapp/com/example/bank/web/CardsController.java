package com.example.bank.web;

import com.example.bank.api.CardsApi;
import com.example.bank.dto.CardDto;
import java.util.List;
import org.springframework.http.HttpEntity;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** Implementa la interfaz anotada sin repetir las anotaciones. addCard se hereda tal cual. */
@RestController
public class CardsController implements CardsApi {

    @Override
    public ResponseEntity<CardDto> getCard(String cardId) {
        return ResponseEntity.ok(null);
    }

    @Override
    public ResponseEntity<Void> deleteCard(String cardId) {
        return ResponseEntity.ok(null);
    }

    @PostMapping("/cards/import")
    public int importCards(HttpEntity<List<CardDto>> entity) {
        return 0;
    }
}
