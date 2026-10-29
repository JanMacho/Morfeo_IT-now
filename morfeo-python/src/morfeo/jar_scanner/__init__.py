"""Descubre los endpoints HTTP de un .jar o .war (Spring Web MVC y Javalin) sin ejecutarlo.

Lee el bytecode con un parser propio del formato .class; no necesita un JDK.
"""

from morfeo.jar_scanner.scanner import load_endpoints, scan_jar, write_endpoints

__all__ = ["scan_jar", "write_endpoints", "load_endpoints"]
