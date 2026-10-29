package org.springframework.http;

public class HttpEntity<T> {
    private final T body;

    public HttpEntity(T body) {
        this.body = body;
    }

    public T getBody() {
        return body;
    }
}
