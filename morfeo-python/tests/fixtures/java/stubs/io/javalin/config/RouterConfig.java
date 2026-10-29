package io.javalin.config;

import io.javalin.apibuilder.EndpointGroup;

public final class RouterConfig {
    public final RouterConfig apiBuilder(EndpointGroup endpoints) {
        endpoints.addEndpoints();
        return this;
    }
}
