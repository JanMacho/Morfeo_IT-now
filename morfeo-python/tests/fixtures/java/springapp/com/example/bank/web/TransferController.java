package com.example.bank.web;

import com.example.bank.dto.TransferReceipt;
import com.example.bank.dto.TransferRequest;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(path = "/api/transfers", produces = "application/json")
public class TransferController {

    @PostMapping(consumes = "application/json")
    public TransferReceipt create(@RequestBody TransferRequest request) {
        return new TransferReceipt("1", "OK");
    }

    @GetMapping(value = "/rates", params = {"base=EUR", "!debug"}, headers = "X-Api-Version=2")
    public Map<String, Double> rates() {
        return Map.of();
    }
}
