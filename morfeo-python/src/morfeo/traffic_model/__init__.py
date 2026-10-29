"""Modelo de tráfico: estadística base, cadena de Markov, curva horaria y validación.

Flujo típico::

    requests, dropped = only_matched(read_requests_csv("requests.csv"))
    train_df, test_df = split_holdout(requests, 0.3)
    profile = train(train_df, timezone="Europe/Madrid", holdout=0.3)
    print(format_report(validate(profile, test_df)))
"""

from morfeo.traffic_model.distributions import build_dist, sample_from_dist
from morfeo.traffic_model.ks import ks_2samp
from morfeo.traffic_model.markov import sample_sessions
from morfeo.traffic_model.model import load_profile, only_matched, split_holdout, train, write_profile
from morfeo.traffic_model.validate import format_report, validate

__all__ = [
    "build_dist",
    "format_report",
    "ks_2samp",
    "load_profile",
    "only_matched",
    "sample_from_dist",
    "sample_sessions",
    "split_holdout",
    "train",
    "validate",
    "write_profile",
]
