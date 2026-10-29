package com.example.bank.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

/** Objeto de formulario al estilo de PetClinic (sin @RequestBody). */
public class Customer {
    private Integer id;

    @NotBlank
    private String firstName;

    @NotBlank
    private String lastName;

    private String address;

    @Pattern(regexp = "\\d{10}")
    private String telephone;

    public void setFirstName(String firstName) { this.firstName = firstName; }
}
