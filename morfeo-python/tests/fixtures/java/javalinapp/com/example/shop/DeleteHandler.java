package com.example.shop;

import io.javalin.http.Context;
import io.javalin.http.Handler;

public class DeleteHandler implements Handler {
    @Override
    public void handle(Context ctx) {
        String id = ctx.pathParam("id");
        String session = ctx.cookie("session");
        ctx.status(204);
    }
}
