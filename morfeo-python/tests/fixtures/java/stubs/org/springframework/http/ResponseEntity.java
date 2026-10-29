package org.springframework.http;

public class ResponseEntity<T> extends HttpEntity<T> {
    public ResponseEntity(T body) {
        super(body);
    }

    public static <T> ResponseEntity<T> ok(T body) {
        return new ResponseEntity<>(body);
    }
}
