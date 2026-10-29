"""Análisis de logs de uso: requests.csv y analysis.json.

Flujo típico::

    config = load_parser_config("auto")
    logs = read_logs(["trafico.log"], config)
    result = analyze(logs, config=config, endpoints=endpoints_doc)
    write_analysis(result, "build")

``generate_synthetic_logs`` crea logs de prueba con un ground truth conocido.
"""

from __future__ import annotations

from morfeo.log_analyzer.analyzer import AnalysisResult, analyze, write_analysis
from morfeo.log_analyzer.config import ParserConfig, detect_format, load_parser_config
from morfeo.log_analyzer.normalize import EndpointMatcher, heuristic_template
from morfeo.log_analyzer.parsers import LogParseError, read_logs, resolve_config
from morfeo.log_analyzer.synthetic import generate_synthetic_logs

__all__ = [
    "AnalysisResult",
    "EndpointMatcher",
    "LogParseError",
    "ParserConfig",
    "analyze",
    "detect_format",
    "generate_synthetic_logs",
    "heuristic_template",
    "load_parser_config",
    "read_logs",
    "resolve_config",
    "write_analysis",
]
