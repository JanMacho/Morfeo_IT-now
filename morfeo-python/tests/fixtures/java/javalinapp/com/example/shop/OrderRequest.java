package com.example.shop;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.math.BigDecimal;
import java.util.List;

public class OrderRequest {
    @JsonProperty("customer_id")
    private String customerId;
    private List<String> products;
    private BigDecimal total;
}
