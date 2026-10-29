package io.javalin;

import io.javalin.config.JavalinConfig;
import io.javalin.router.JavalinDefaultRoutingApi;
import java.util.function.Consumer;

public class Javalin implements JavalinDefaultRoutingApi<Javalin> {
    protected Javalin(JavalinConfig config) {
    }

    public static Javalin create() {
        return new Javalin(new JavalinConfig());
    }

    public static Javalin create(Consumer<JavalinConfig> config) {
        JavalinConfig cfg = new JavalinConfig();
        config.accept(cfg);
        return new Javalin(cfg);
    }

    public Javalin start(int port) {
        return this;
    }
}
