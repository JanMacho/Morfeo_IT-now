package com.example.bank.dto;

import jakarta.validation.constraints.FutureOrPresent;
import jakarta.validation.constraints.PastOrPresent;
import java.time.ZonedDateTime;
import java.util.Date;

/** Anidamiento de cinco niveles para comprobar el corte por profundidad. */
public class Deep {
    private L1 first;

    public static class L1 { private L2 next; @PastOrPresent private Date since; }
    public static class L2 { private L3 next; @FutureOrPresent private ZonedDateTime until; }
    public static class L3 { private L4 next; }
    public static class L4 { private L5 next; }
    public static class L5 { private String leaf; }
}
