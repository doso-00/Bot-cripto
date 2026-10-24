"""Controllo qualità delle candele (brief §4.4) e report in markdown.

Uso:
    uv run python -m bot.data.quality

Esito:
- ERRORI (bloccano il backtest): duplicati, valori nulli, prezzi <= 0, volume negativo,
  high < low, open/close fuori dal range [low, high], open_time non allineato al
  timeframe, close_time incoerente, storico che inizia dopo history_start, buchi non
  spiegati, prezzi del timeframe lungo diversi da quelli ricostruiti dal timeframe corto.
- AVVISI (da leggere, non bloccano): salti di prezzo oltre soglia, open lontano dal close
  precedente, candele a volume zero, volume del timeframe lungo diverso dalla somma del
  timeframe corto, dati non aggiornati.
- SPIEGATI: buchi e anomalie che cadono dentro un evento noto di config/known_events.yaml
  (es. un fermo dell'exchange). Restano nel report, ma non bloccano.
Exit code 1 se ci sono errori o buchi non spiegati.
"""

import logging
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from bot.config import Settings, load_settings
from bot.data.db import connect, load_candles
from bot.data.klines import timeframe_ms
from bot.logging_setup import setup_logging

log = logging.getLogger(__name__)

ERROR = "errore"
WARNING = "avviso"
EXPLAINED = "spiegato"
MAX_EXAMPLES = 5

_PRICE_COLUMNS = ["open", "high", "low", "close"]
_REQUIRED_COLUMNS = [*_PRICE_COLUMNS, "volume", "close_time"]


@dataclass
class Issue:
    severity: str
    check: str
    symbol: str
    timeframe: str
    count: int
    examples: list[str] = field(default_factory=list)


@dataclass
class Gap:
    symbol: str
    timeframe: str
    first_missing: datetime
    last_missing: datetime
    n_missing: int
    explanation: str | None = None
    common_to_all_symbols: bool = False


@dataclass
class KnownEvent:
    """Intervallo di tempo [start, end) in cui i dati sono anomali per una causa nota."""

    symbol: str          # "*" = tutti i simboli
    timeframe: str       # "*" = tutti i timeframe
    start: datetime
    end: datetime
    reason: str
    source: str | None = None

    def applies_to(self, symbol: str, timeframe: str) -> bool:
        return self.symbol in ("*", symbol) and self.timeframe in ("*", timeframe)

    def window(self, timeframe: str) -> tuple[pd.Timestamp, pd.Timestamp]:
        """L'intervallo allargato ai confini delle candele del timeframe.

        Una candela 4h che contiene anche solo un'ora di fermo è toccata dal fermo:
        per questo l'inizio si arrotonda per difetto e la fine per eccesso.
        """
        step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        return pd.Timestamp(self.start).floor(step), pd.Timestamp(self.end).ceil(step)

    def describe(self) -> str:
        return f"{self.reason} ({self.source})" if self.source else self.reason


@dataclass
class SeriesSummary:
    symbol: str
    timeframe: str
    n_candles: int
    first: datetime | None
    last: datetime | None


@dataclass
class QualityReport:
    generated_at: datetime
    summaries: list[SeriesSummary] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    resample_checks: list[str] = field(default_factory=list)

    def by_severity(self, severity: str) -> list[Issue]:
        return [i for i in self.issues if i.severity == severity]

    @property
    def errors(self) -> list[Issue]:
        return self.by_severity(ERROR)

    @property
    def warnings(self) -> list[Issue]:
        return self.by_severity(WARNING)

    @property
    def explained(self) -> list[Issue]:
        return self.by_severity(EXPLAINED)

    @property
    def unexplained_gaps(self) -> list[Gap]:
        return [g for g in self.gaps if g.explanation is None]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.unexplained_gaps


def _issue(severity, check, symbol, timeframe, rows: pd.DataFrame, describe=None) -> list[Issue]:
    if rows.empty:
        return []
    describe = describe or (lambda r: str(r.open_time))
    examples = [describe(r) for r in rows.head(MAX_EXAMPLES).itertuples()]
    return [Issue(severity, check, symbol, timeframe, len(rows), examples)]


def check_candles(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str,
    history_start: date | None,
    max_abs_return: float,
    max_open_gap: float,
    events: Iterable[KnownEvent] = (),
) -> tuple[list[Issue], list[Gap]]:
    """Controlli su una singola serie (simbolo, timeframe). `df` ordinato o no, indifferente."""
    if df.empty:
        return [Issue(ERROR, "nessuna candela", symbol, timeframe, 0)], []

    step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    events = [e for e in events if e.applies_to(symbol, timeframe)]
    df = df.sort_values("open_time").reset_index(drop=True)
    issues: list[Issue] = []

    def add(severity, check, mask, describe=None):
        issues.extend(_issue(severity, check, symbol, timeframe, df[mask], describe))

    # Candele interamente contenute in un evento noto (es. a cavallo dell'inizio di un fermo)
    in_event = pd.Series(False, index=df.index)
    for e in events:
        start, end = e.window(timeframe)
        in_event |= (df["open_time"] >= start) & (df["open_time"] + step <= end)

    add(ERROR, "open_time duplicato", df.duplicated("open_time", keep=False))
    for col in _REQUIRED_COLUMNS:
        add(ERROR, f"valore nullo in {col}", df[col].isna())
    for col in _PRICE_COLUMNS:
        add(ERROR, f"{col} <= 0", df[col] <= 0)
    add(ERROR, "volume negativo", df["volume"] < 0)
    add(ERROR, "high < low", df["high"] < df["low"], lambda r: f"{r.open_time} high={r.high} low={r.low}")
    for col in ("open", "close"):
        add(
            ERROR,
            f"{col} fuori dal range [low, high]",
            (df[col] < df["low"]) | (df[col] > df["high"]),
            lambda r, c=col: f"{r.open_time} {c}={getattr(r, c)} low={r.low} high={r.high}",
        )
    epoch = pd.Timestamp("1970-01-01")
    add(ERROR, "open_time non allineato al timeframe", ((df["open_time"] - epoch) % step) != pd.Timedelta(0))

    # Quando l'exchange si ferma, l'ultima candela prima del fermo si chiude in anticipo:
    # close_time anomalo. Dentro un evento noto è atteso, fuori è un errore.
    bad_close = df["close_time"] != df["open_time"] + step - pd.Timedelta(milliseconds=1)
    describe_close = lambda r: f"{r.open_time} close_time={r.close_time} volume={r.volume}"  # noqa: E731
    add(ERROR, "close_time diverso da open_time + intervallo - 1 ms", bad_close & ~in_event, describe_close)
    add(EXPLAINED, "close_time anomalo durante un evento noto", bad_close & in_event, describe_close)

    if history_start is not None and df["open_time"].iat[0] > pd.Timestamp(history_start):
        issues.append(
            Issue(ERROR, "storico inizia dopo history_start", symbol, timeframe, 1,
                  [f"prima candela {df['open_time'].iat[0]}, attesa {history_start}"])
        )

    # Avvisi: confronti con la candela precedente, solo se è davvero la precedente (niente buco)
    prev_close = df["close"].shift()
    consecutive = (df["open_time"] - df["open_time"].shift()) == step
    ret = df["close"] / prev_close - 1
    open_gap = df["open"] / prev_close - 1
    add(
        WARNING,
        f"variazione close-to-close oltre {max_abs_return:.0%}",
        consecutive & (ret.abs() > max_abs_return),
        lambda r: f"{r.open_time} variazione {ret[r.Index]:+.2%}",
    )
    add(
        WARNING,
        f"open distante oltre {max_open_gap:.0%} dal close precedente",
        consecutive & (open_gap.abs() > max_open_gap),
        lambda r: f"{r.open_time} distanza {open_gap[r.Index]:+.2%}",
    )
    zero_volume = df["volume"] == 0
    add(WARNING, "candela a volume zero", zero_volume & ~in_event)
    add(EXPLAINED, "candela a volume zero durante un evento noto", zero_volume & in_event)

    gaps = find_gaps(df["open_time"], step, symbol, timeframe)
    for gap in gaps:
        gap.explanation = explain_range(events, timeframe, gap.first_missing, gap.last_missing + step)
    return issues, gaps


def explain_range(events: Iterable[KnownEvent], timeframe: str, start: datetime, end: datetime) -> str | None:
    """Motivo del primo evento che contiene interamente l'intervallo [start, end)."""
    for e in events:
        w_start, w_end = e.window(timeframe)
        if w_start <= pd.Timestamp(start) and pd.Timestamp(end) <= w_end:
            return e.describe()
    return None


def find_gaps(open_times: pd.Series, step: pd.Timedelta, symbol: str, timeframe: str) -> list[Gap]:
    """Intervalli di candele mancanti tra la prima e l'ultima candela presenti."""
    times = open_times.drop_duplicates().sort_values().reset_index(drop=True)
    delta = times.diff()
    gaps = []
    for i in delta.index[delta > step]:
        n_missing = int(delta[i] / step) - 1
        gaps.append(
            Gap(symbol, timeframe, (times[i - 1] + step).to_pydatetime(), (times[i] - step).to_pydatetime(), n_missing)
        )
    return gaps


def check_resample(
    fine: pd.DataFrame, coarse: pd.DataFrame, symbol: str, fine_tf: str, coarse_tf: str, tolerance: float
) -> tuple[list[Issue], str]:
    """Confronta le candele del timeframe lungo con quelle ricostruite dal timeframe corto.

    Si confrontano solo i blocchi in cui tutte le candele corte sono presenti: un blocco
    incompleto non è ricostruibile e viene contato a parte. Una differenza di prezzo è un
    errore; una differenza solo di volume è un avviso (nessuna strategia usa il volume).
    """
    ratio = timeframe_ms(coarse_tf) // timeframe_ms(fine_tf)
    bucket = fine["open_time"].dt.floor(pd.Timedelta(milliseconds=timeframe_ms(coarse_tf)))
    agg = (
        fine.sort_values("open_time")
        .groupby(bucket)
        .agg(n=("open", "size"), open=("open", "first"), high=("high", "max"),
             low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    )
    complete = agg[agg["n"] == ratio]
    merged = coarse.set_index("open_time").join(complete, how="inner", suffixes=("", "_rebuilt"))

    def differs(col: str) -> pd.Series:
        rel = (merged[col] - merged[f"{col}_rebuilt"]).abs() / merged[col].abs().clip(lower=1e-12)
        return rel > tolerance

    price_mismatch = pd.Series(False, index=merged.index)
    for col in _PRICE_COLUMNS:
        price_mismatch |= differs(col)
    volume_only = differs("volume") & ~price_mismatch

    def examples(rows: pd.DataFrame) -> list[str]:
        return [
            f"{t}: {coarse_tf} o/h/l/c/v={r.open}/{r.high}/{r.low}/{r.close}/{r.volume:.4f}, "
            f"da {fine_tf}={r.open_rebuilt}/{r.high_rebuilt}/{r.low_rebuilt}/{r.close_rebuilt}/{r.volume_rebuilt:.4f}"
            for t, r in rows.head(MAX_EXAMPLES).iterrows()
        ]

    issues = []
    for severity, mask, check in (
        (ERROR, price_mismatch, f"prezzi {coarse_tf} diversi dalla ricostruzione da {fine_tf}"),
        (WARNING, volume_only, f"volume {coarse_tf} diverso dalla somma delle {fine_tf} (prezzi identici)"),
    ):
        if mask.any():
            issues.append(Issue(severity, check, symbol, coarse_tf, int(mask.sum()), examples(merged[mask])))
    skipped = len(coarse) - len(merged)
    note = (
        f"{symbol} {coarse_tf} vs {fine_tf}: {len(merged)} candele confrontate, "
        f"{int(price_mismatch.sum())} con prezzi diversi, {int(volume_only.sum())} solo con volume diverso, "
        f"{skipped} non confrontabili (blocco {fine_tf} incompleto o assente)"
    )
    return issues, note


def load_known_events(path: Path) -> list[KnownEvent]:
    if not path.exists():
        return []
    entries = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("known_events") or []
    events = []
    for e in entries:
        for key in ("symbol", "timeframe", "start", "end", "reason"):
            if key not in e:
                raise ValueError(f"{path.name}: voce senza '{key}': {e}")
        event = KnownEvent(
            symbol=e["symbol"], timeframe=e["timeframe"],
            start=pd.Timestamp(e["start"]).to_pydatetime(), end=pd.Timestamp(e["end"]).to_pydatetime(),
            reason=e["reason"], source=e.get("source"),
        )
        if event.start >= event.end:
            raise ValueError(f"{path.name}: start deve precedere end: {e}")
        events.append(event)
    return events


def mark_common_gaps(gaps: Iterable[Gap], symbols: list[str]) -> None:
    """Marca i buchi presenti, nello stesso intervallo, in tutti i simboli.

    È un indizio di fermo dell'exchange, non una spiegazione: va comunque registrato
    un evento in known_events.yaml.
    """
    gaps = list(gaps)
    for gap in gaps:
        others = {
            g.symbol for g in gaps
            if g.timeframe == gap.timeframe and g.symbol != gap.symbol
            and g.first_missing <= gap.first_missing and g.last_missing >= gap.last_missing
        }
        gap.common_to_all_symbols = len(symbols) > 1 and len(others) == len(symbols) - 1


def run_quality(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    now: datetime | None = None,
) -> QualityReport:
    symbols = symbols or settings.symbols
    timeframes = timeframes or settings.timeframes
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    report = QualityReport(generated_at=now)
    q = settings.data_quality
    events = load_known_events(settings.paths.known_events)

    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in symbols:
        for tf in timeframes:
            df = load_candles(con, symbol, tf)
            frames[symbol, tf] = df
            report.summaries.append(
                SeriesSummary(symbol, tf, len(df), *(df["open_time"].agg(["min", "max"]) if len(df) else (None, None)))
            )
            if tf not in q.max_abs_return:
                raise ValueError(f"data_quality.max_abs_return non definito per il timeframe {tf}")
            issues, gaps = check_candles(
                df, symbol, tf, settings.history_start, q.max_abs_return[tf], q.max_open_gap, events
            )
            report.issues += issues
            report.gaps += gaps
            if len(df) and df["open_time"].iat[-1] < pd.Timestamp(now) - 3 * pd.Timedelta(milliseconds=timeframe_ms(tf)):
                report.issues.append(
                    Issue(WARNING, "dati non aggiornati", symbol, tf, 1, [f"ultima candela {df['open_time'].iat[-1]}"])
                )

        ordered = sorted(timeframes, key=timeframe_ms)
        for fine_tf, coarse_tf in zip(ordered, ordered[1:]):
            if timeframe_ms(coarse_tf) % timeframe_ms(fine_tf) == 0 and len(frames[symbol, fine_tf]) and len(frames[symbol, coarse_tf]):
                issues, note = check_resample(
                    frames[symbol, fine_tf], frames[symbol, coarse_tf], symbol, fine_tf, coarse_tf, q.resample_tolerance
                )
                report.issues += issues
                report.resample_checks.append(note)

    mark_common_gaps(report.gaps, symbols)
    return report


def render_markdown(report: QualityReport) -> str:
    n_explained_gaps = len(report.gaps) - len(report.unexplained_gaps)
    lines = [
        f"# Report qualità dati — {report.generated_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"**Esito: {'OK' if report.ok else 'NON OK'}** — {len(report.errors)} controlli con errori, "
        f"{len(report.unexplained_gaps)} buchi non spiegati su {len(report.gaps)}, {len(report.warnings)} avvisi, "
        f"{len(report.explained)} anomalie e {n_explained_gaps} buchi spiegati da eventi noti.",
        "",
        "## Serie",
        "",
        "| Simbolo | Timeframe | Candele | Prima | Ultima |",
        "|---|---|---|---|---|",
        *(f"| {s.symbol} | {s.timeframe} | {s.n_candles} | {s.first} | {s.last} |" for s in report.summaries),
    ]
    for title, items in (
        ("Errori", report.errors),
        ("Avvisi", report.warnings),
        ("Anomalie spiegate da eventi noti", report.explained),
    ):
        lines += ["", f"## {title}", ""]
        if not items:
            lines.append("Nessuno.")
        for i in items:
            lines.append(f"- **{i.symbol} {i.timeframe} — {i.check}**: {i.count} casi")
            lines += [f"  - {e}" for e in i.examples]
            if i.count > len(i.examples):
                lines.append(f"  - ... e altri {i.count - len(i.examples)}")

    lines += ["", "## Buchi (candele mancanti)", ""]
    if not report.gaps:
        lines.append("Nessuno.")
    else:
        lines += [
            "| Simbolo | Timeframe | Prima mancante | Ultima mancante | N | Comune a tutti i simboli | Spiegazione |",
            "|---|---|---|---|---|---|---|",
            *(
                f"| {g.symbol} | {g.timeframe} | {g.first_missing} | {g.last_missing} | {g.n_missing} | "
                f"{'sì' if g.common_to_all_symbols else 'no'} | {g.explanation or '**NON SPIEGATO**'} |"
                for g in report.gaps
            ),
        ]
    lines += ["", "## Confronto tra timeframe", ""]
    lines += [f"- {n}" for n in report.resample_checks] or ["Non eseguito."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    setup_logging(settings.paths.logs, "quality")
    if not settings.paths.database.exists():
        log.error("database %s assente: eseguire prima uv run python -m bot.data.download", settings.paths.database)
        return 1
    con = connect(settings.paths.database, read_only=True)
    report = run_quality(con, settings)
    con.close()

    settings.paths.reports.mkdir(parents=True, exist_ok=True)
    out = settings.paths.reports / f"data_quality_{report.generated_at:%Y%m%d_%H%M}.md"
    text = render_markdown(report)
    out.write_text(text, encoding="utf-8")
    print(text)
    log.info("report salvato in %s", out)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
