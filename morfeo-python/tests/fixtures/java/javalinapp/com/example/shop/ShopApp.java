package com.example.shop;

import static io.javalin.apibuilder.ApiBuilder.get;
import static io.javalin.apibuilder.ApiBuilder.path;
import static io.javalin.apibuilder.ApiBuilder.post;

import io.javalin.Javalin;
import io.javalin.http.Context;
import java.util.List;

/** App Javalin de ejemplo: lambdas, referencias a metodo, clase Handler y ApiBuilder anidado. */
public class ShopApp {

    public static void main(String[] args) {
        Javalin app = Javalin.create(config -> config.router.apiBuilder(() -> {
            path("/admin", () -> {
                get("/stats", ctx -> ctx.json(List.of()));
                post(ctx -> ctx.result(ctx.formParam("note")));
                path("users", () -> get("/<userId>", ctx -> ctx.result(ctx.pathParam("userId"))));
            });
        }));
        app.before(ctx -> ctx.attribute("start", System.nanoTime()));
        app.get("/api/products", ctx -> {
            Integer page = ctx.queryParamAsClass("page", Integer.class).getOrDefault(1);
            ctx.header("X-Page", String.valueOf(page));
            ctx.json(List.of());
        });
        app.get("/api/products/<id>", ShopApp::getProduct);
        app.post("/api/orders", ctx -> {
            OrderRequest request = ctx.bodyAsClass(OrderRequest.class);
            ctx.status(201).json(request);
        });
        app.put("/api/orders/{id}", ctx -> handleUpdate(ctx));
        app.delete("/api/orders/{id}", new DeleteHandler());
        // ruta construida en tiempo de ejecución: el escáner la omite con un aviso
        String base = args.length > 0 ? args[0] : "/api/v2";
        app.get(base + "/stock", ctx -> ctx.result("0"));
        app.start(8080);
    }

    static void getProduct(Context ctx) {
        String id = ctx.pathParam("id");
        String currency = ctx.queryParam("currency");
        String trace = ctx.header("X-Trace");
        ctx.result(id + currency + trace);
    }

    private static void handleUpdate(Context ctx) {
        String id = ctx.pathParam("id");
        OrderRequest request = parseOrder(ctx);
        ctx.json(request);
    }

    private static OrderRequest parseOrder(Context ctx) {
        return ctx.bodyAsClass(OrderRequest.class);
    }
}
