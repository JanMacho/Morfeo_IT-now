package com.example.bank.dto;

import java.util.List;

/** Envoltorio generico: T se resuelve con el argumento de tipo del parametro. */
public class BatchRequest<T> {
    private List<T> items;
    private boolean atomic;
}
