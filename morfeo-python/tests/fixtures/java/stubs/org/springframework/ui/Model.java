package org.springframework.ui;

public interface Model {
    Model addAttribute(String name, Object value);
}
