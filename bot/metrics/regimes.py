"""Analisi per regime di mercato (brief §6.2 punto 5).

Definizione fissata il 2026-10-23 prima di vedere i risultati (docs/DECISIONI.md):
un giorno è rialzista se il rendimento delle 90 giornate precedenti supera +15%,
ribassista se è sotto -15%, laterale altrimenti. L'etichetta del giorno d usa solo i
close fino al giorno d-1, quindi è calcolabile anche in tempo reale.
"""

import pandas as pd

BULL, BEAR, SIDEWAYS, UNKNOWN = "rialzista", "ribassista", "laterale", "non classificato"


def label_days(candles: pd.DataFrame, lookback_days: int, threshold: float) -> pd.Series:
    """Etichetta di regime per ogni giorno UTC (indice: date)."""
    daily_close = candles.groupby(candles["open_time"].dt.normalize())["close"].last()
    daily_close = daily_close.asfreq("D").ffill()  # i giorni con buchi prendono l'ultimo close noto
    trailing = daily_close / daily_close.shift(lookback_days) - 1
    labels = pd.Series(SIDEWAYS, index=trailing.index)
    labels[trailing > threshold] = BULL
    labels[trailing < -threshold] = BEAR
    labels[trailing.isna()] = UNKNOWN
    return labels.shift(1).fillna(UNKNOWN)  # il giorno d usa i dati fino a d-1


def regime_performance(equity_curves: dict[str, pd.DataFrame], labels: pd.Series) -> pd.DataFrame:
    """Rendimento composto di ogni curva di equity dentro ciascun regime."""
    rows = []
    first = next(iter(equity_curves.values()))
    day_label = first["open_time"].dt.normalize().map(labels).fillna(UNKNOWN)
    total = len(first)
    for regime in (BULL, BEAR, SIDEWAYS, UNKNOWN):
        mask = day_label == regime
        if not mask.any():
            continue
        row = {"regime": regime, "share_of_time": mask.sum() / total, "days": first.loc[mask, "open_time"].dt.normalize().nunique()}
        for name, eq in equity_curves.items():
            returns = eq["equity"].pct_change().fillna(0)
            row[name] = (1 + returns[mask]).prod() - 1
        rows.append(row)
    return pd.DataFrame(rows)
