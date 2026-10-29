package io.javalin.http;

import io.javalin.validation.Validator;
import java.util.List;

/** Subconjunto de io.javalin.http.Context (Javalin 6.3) con las mismas firmas. */
public interface Context {
    default String body() { return null; }
    default <T> T bodyAsClass(Class<T> type) { return null; }
    default <T> T bodyAsClass(java.lang.reflect.Type type) { return null; }
    default String formParam(String key) { return null; }
    default <T> Validator<T> formParamAsClass(String key, Class<T> type) { return null; }
    String pathParam(String key);
    default <T> Validator<T> pathParamAsClass(String key, Class<T> type) { return null; }
    default String queryParam(String key) { return null; }
    default <T> Validator<T> queryParamAsClass(String key, Class<T> type) { return null; }
    default List<String> queryParams(String key) { return null; }
    default String cookie(String name) { return null; }
    default Context cookie(String name, String value) { return this; }
    default String header(String header) { return null; }
    default Context header(String name, String value) { return this; }
    default <T> T attribute(String key) { return null; }
    default void attribute(String key, Object value) { }
    default Context status(int status) { return this; }
    default Context result(String result) { return this; }
    default Context json(Object obj) { return this; }
}
