package io.javalin.router;

import io.javalin.http.Handler;

/** En Javalin 6 get/post/... son metodos default de esta interfaz; el sitio de llamada usa Javalin como owner. */
public interface JavalinDefaultRoutingApi<API extends RoutingApi> extends RoutingApi {
    default API get(String path, Handler handler) { return null; }
    default API post(String path, Handler handler) { return null; }
    default API put(String path, Handler handler) { return null; }
    default API patch(String path, Handler handler) { return null; }
    default API delete(String path, Handler handler) { return null; }
    default API head(String path, Handler handler) { return null; }
    default API options(String path, Handler handler) { return null; }
    default API before(Handler handler) { return null; }
    default API after(Handler handler) { return null; }
}
