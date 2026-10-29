package io.javalin.apibuilder;

import io.javalin.http.Handler;

public class ApiBuilder {
    public static void path(String path, EndpointGroup group) { group.addEndpoints(); }
    public static void get(String path, Handler handler) { }
    public static void get(Handler handler) { }
    public static void post(String path, Handler handler) { }
    public static void post(Handler handler) { }
    public static void put(String path, Handler handler) { }
    public static void put(Handler handler) { }
    public static void patch(String path, Handler handler) { }
    public static void patch(Handler handler) { }
    public static void delete(String path, Handler handler) { }
    public static void delete(Handler handler) { }
    public static void before(Handler handler) { }
}
