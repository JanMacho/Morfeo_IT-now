package com.example.bank.web;

import com.example.bank.api.CrudApi;
import com.example.bank.dto.TagDto;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/tags")
public class TagController implements CrudApi<TagDto, Long> {
    @Override
    public ResponseEntity<TagDto> replace(Long id, TagDto body) {
        return ResponseEntity.ok(body);
    }
}
