"""Plan de JMeter (.jmx) a partir del perfil de tráfico y de los endpoints.

Flujo típico::

    from morfeo.jmeter_plan import Options, convert, load_user_pool

    plan = convert(profile, endpoints, Options(mode="steps"), load_user_pool("requests.csv", 200, []))
    open("plan.jmx", "wb").write(plan.xml)

``Options`` reúne todos los parámetros del plan. Es el único sitio que habría
que alimentar para que otro componente decida esos parámetros.
"""

from morfeo.jmeter_plan.plan import Options, Plan, UserPool, convert, load_user_pool

__all__ = ["Options", "Plan", "UserPool", "convert", "load_user_pool"]
