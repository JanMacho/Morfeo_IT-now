package io.javalin.http;

@FunctionalInterface
public interface Handler {
    void handle(Context ctx) throws Exception;
}
