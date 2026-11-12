"""Validatore della revisione di Claude: `uv run python -m stocks.apply_review`.

Claude può spiegare e restringere, mai allentare: ogni proposta passa da regole di
ammissione e solo quelle ammesse modificano piani e segnali. Nessun tipo di proposta
può creare un COMPRA, aumentare un importo o abbassare uno stop.

Rifiutare è sempre sicuro: una proposta non valida finisce nelle revisioni come
rifiutata (con il motivo) e non cambia nulla. Revisione assente, malformata o più
vecchia di 12 ore = modalità degradata normale (Review Focus 4): la pagina esce con i
soli segnali del codice e `review_available = False`; il comando termina comunque con 0.
"""

import argparse
import logging
import math
import sys
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from stocks.data import to_eur
from stocks.models import Action, Plan, Snapshot, TickerSnapshot
from stocks.review import ClaudeReview, GlossaryItem, Proposal, TickerReview
from stocks.signals import _round_eur, opportunity_scenarios
from stocks.state import JournalEntry, PlanRevision, State, load_state, save_state

logger = logging.getLogger("stocks.apply_review")

PAGE_TZ = ZoneInfo("Europe/Rome")
SNAPSHOT_FILE = "snapshot.json"
REVIEW_FILE = "claude_review.json"
FINAL_FILE = "final.json"

# Oltre queste ore la revisione è considerata vecchia: notizie stantie non si applicano.
MAX_REVIEW_AGE = timedelta(hours=12)
# Tolleranza sull'orologio: una revisione datata oltre questo margine nel futuro è sospetta.
MAX_REVIEW_FUTURE = timedelta(minutes=10)
# Proroga massima della data limite, in giorni.
MAX_EXTEND_DAYS = 30
NO_POSITION_REASON = "nessuna posizione aperta"


class FinalView(BaseModel):
    """Snapshot dopo la revisione, più ciò che serve alla pagina.

    `snapshot` è una copia modificata (l'originale non viene toccato); `reviews` contiene
    solo i titoli presenti nello snapshot; `watched` elenca i ticker con l'etichetta
    "da guardare" (proposta `watch`, solo un'etichetta: non cambia il segnale).
    Glossario e nota di mercato sono vuoti se la revisione non è disponibile.
    """

    snapshot: Snapshot
    reviews: dict[str, TickerReview] = {}
    applied: list[PlanRevision] = []
    rejected: list[PlanRevision] = []
    review_available: bool
    watched: list[str] = []
    glossary: list[GlossaryItem] = []
    market_note: str = ""


class _Rejected(Exception):
    """Proposta non ammessa: il messaggio è il motivo in italiano."""


def _fmt(value) -> str | None:
    return None if value is None else str(value)


def _parse_float(value: str | None) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _Rejected(f"valore non interpretabile come numero: {value!r}") from None
    if not math.isfinite(number):
        raise _Rejected(f"valore non finito: {value!r}")
    return number


def _parse_date(value: str | None) -> date:
    try:
        return date.fromisoformat(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise _Rejected(
            f"valore non interpretabile come data AAAA-MM-GG: {value!r}"
        ) from None


def _tightened(plan: Plan, new_stop: float, eurusd: float) -> Plan:
    """Piano con lo stop alzato.

    Lo stop in euro è quello da inserire su Trade Republic e va fissato col cambio del
    momento (stesso arrotondamento per difetto del motore), ma non può mai scendere:
    se il cambio si è mosso contro, resta il valore precedente. Mai None su un valore esistente.
    """
    stop_eur = plan.stop_eur
    try:
        candidate = _round_eur(to_eur(new_stop, plan.currency, eurusd), ROUND_FLOOR)
        stop_eur = candidate if stop_eur is None else max(candidate, stop_eur)
    except ValueError:  # valuta non supportata: si tiene il valore precedente
        pass
    return _with_fresh_scenarios(
        plan.model_copy(update={"stop": new_stop, "stop_eur": stop_eur})
    )


def _with_fresh_scenarios(plan: Plan) -> Plan:
    """Rigenera i testi degli scenari dopo una modifica di stop o scadenza.

    Solo per i piani con stop, obiettivo e scadenza (occasione/evento); gli altri non hanno
    testi che citano questi livelli.
    """
    if None in (plan.stop, plan.stop_eur, plan.target, plan.target_eur, plan.deadline):
        return plan
    scenarios = opportunity_scenarios(
        plan.stop,
        plan.stop_eur,
        plan.target,
        plan.target_eur,
        plan.deadline,
        plan.currency,
    )
    return plan.model_copy(update={"scenarios": scenarios})


def _commit_plan(state: State, ticker: TickerSnapshot, plan: Plan) -> None:
    """Scrive il piano nello stato e una copia identica nel segnale della vista."""
    state.plans[ticker.ticker] = plan
    ticker.signal.plan = plan.model_copy(deep=True)


def _evaluate(
    proposal: Proposal,
    ticker: TickerSnapshot,
    state: State,
    today: date,
    eurusd: float,
    view_watched: set[str],
) -> tuple[str, str | None, str | None]:
    """Valuta e applica una proposta. Ritorna (campo, vecchio, nuovo) o solleva _Rejected."""
    kind = proposal.type
    signal = ticker.signal

    if kind == "watch":
        view_watched.add(ticker.ticker)
        return "watch", None, "true"

    if kind == "downgrade_buy":
        if signal.action not in (Action.COMPRA, Action.ACCUMULA):
            raise _Rejected(f"l'azione è {signal.action.value}, non un acquisto")
        old = signal.action.value
        signal.action = Action.NESSUNA_AZIONE
        signal.amount_eur = None
        signal.kind = None
        signal.plan = None
        signal.reasons = [
            *signal.reasons,
            f"Declassato dalla revisione notizie: {proposal.reason}",
        ]
        return "action", old, signal.action.value

    # Tutti gli altri tipi riguardano solo posizioni aperte con un piano.
    plan = state.plans.get(ticker.ticker)
    if ticker.holding is None or plan is None:
        raise _Rejected(NO_POSITION_REASON)

    if kind == "request_review":
        if signal.action in (Action.VENDI, Action.RIDUCI, Action.DATI_MANCANTI):
            raise _Rejected(
                f"l'azione {signal.action.value} non va sostituita da RIVEDI"
            )
        old = signal.action.value
        signal.action = Action.RIVEDI
        signal.amount_eur = None
        return "action", old, signal.action.value

    if kind == "tighten_stop":
        new_stop = _parse_float(proposal.value)
        if plan.stop is None:
            raise _Rejected("il piano non ha uno stop da alzare")
        if ticker.close is None:
            raise _Rejected("prezzo di chiusura non disponibile")
        if new_stop <= plan.stop:
            raise _Rejected(
                f"il nuovo stop {new_stop} non è sopra quello attuale {plan.stop}"
            )
        if new_stop >= ticker.close:
            raise _Rejected(
                f"il nuovo stop {new_stop} non è sotto il prezzo {ticker.close}"
            )
        old = _fmt(plan.stop)
        _commit_plan(state, ticker, _tightened(plan, new_stop, eurusd))
        return "stop", old, _fmt(new_stop)

    # shorten_deadline / extend_deadline
    new_deadline = _parse_date(proposal.value)
    if plan.deadline is None:
        raise _Rejected("il piano non ha una data limite")
    old = plan.deadline.isoformat()
    if kind == "shorten_deadline":
        if new_deadline >= plan.deadline:
            raise _Rejected(
                f"la nuova data {new_deadline} non è prima di quella attuale {old}"
            )
        if new_deadline < today:
            raise _Rejected(f"la nuova data {new_deadline} è già passata")
    else:
        if proposal.reason_kind != "evento":
            raise _Rejected("la proroga è ammessa solo per un evento")
        if new_deadline <= plan.deadline:
            raise _Rejected(
                f"la nuova data {new_deadline} non è dopo quella attuale {old}"
            )
        # Tetto cumulativo (R14): dalla data limite del primo piano, non dall'ultima proroga.
        base = plan.original_deadline or plan.deadline
        if new_deadline > base + timedelta(days=MAX_EXTEND_DAYS):
            raise _Rejected(
                f"la proroga supera {MAX_EXTEND_DAYS} giorni dalla data limite originale {base}"
            )
    updated = plan.model_copy(
        update={
            "deadline": new_deadline,
            "original_deadline": plan.original_deadline or plan.deadline,
        }
    )
    _commit_plan(state, ticker, _with_fresh_scenarios(updated))
    return "deadline", old, new_deadline.isoformat()


def _aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=PAGE_TZ)


def apply(
    snapshot: Snapshot, review: ClaudeReview | None, state: State, now: datetime
) -> FinalView:
    """Applica le sole modifiche ammesse e ritorna la vista finale.

    Non modifica `snapshot` (lavora su una copia profonda, `FinalView.snapshot`), ma
    MODIFICA `state`: piani (stop e data limite), `revisions` (una per proposta, accettata
    o rifiutata) e `journal` (una riga per ogni titolo con evento). Spetta al chiamante
    salvarlo. Revisione None, più vecchia di 12 ore o datata nel futuro (>10 minuti): nessuna modifica.
    """
    view_snapshot = snapshot.model_copy(deep=True)

    if review is not None:
        age = _aware(now) - _aware(review.generated_at)
        if age > MAX_REVIEW_AGE:
            logger.warning(
                "Revisione di Claude del %s più vecchia di 12 ore: ignorata",
                review.generated_at,
            )
            review = None
        elif -age > MAX_REVIEW_FUTURE:
            logger.warning(
                "Revisione di Claude datata nel futuro (%s): ignorata",
                review.generated_at,
            )
            review = None
    # Il piano mostrato deve coincidere con quello nello stato (fonte di verità).
    for t in view_snapshot.tickers:
        if t.holding is not None and t.ticker in state.plans:
            t.signal.plan = state.plans[t.ticker].model_copy(deep=True)
    if review is None:
        return FinalView(snapshot=view_snapshot, review_available=False)

    today = now.astimezone(PAGE_TZ).date() if now.tzinfo else now.date()
    by_ticker = {t.ticker: t for t in view_snapshot.tickers}
    applied: list[PlanRevision] = []
    rejected: list[PlanRevision] = []
    reviews: dict[str, TickerReview] = {}
    watched: set[str] = set()

    seen: set[str] = set()
    for tr in review.tickers:
        ticker = by_ticker.get(tr.ticker)
        duplicate = tr.ticker in seen
        seen.add(tr.ticker)
        if duplicate:
            logger.warning(
                "%s: ticker duplicato nella revisione, si tiene il primo", tr.ticker
            )
            ticker = None
        elif ticker is not None:
            reviews[tr.ticker] = tr
        for proposal in tr.proposals:
            field = {
                "tighten_stop": "stop",
                "shorten_deadline": "deadline",
                "extend_deadline": "deadline",
                "request_review": "action",
                "downgrade_buy": "action",
                "watch": "watch",
            }[proposal.type]
            old = new = None
            try:
                if ticker is None:
                    raise _Rejected(
                        "ticker duplicato"
                        if duplicate
                        else "ticker non presente nello snapshot"
                    )
                field, old, new = _evaluate(
                    proposal, ticker, state, today, view_snapshot.eurusd, watched
                )
                accepted, reason = True, proposal.reason
            except _Rejected as exc:
                accepted, reason = False, f"{proposal.reason} [rifiutata: {exc}]"
                logger.warning(
                    "%s: proposta %s rifiutata: %s", tr.ticker, proposal.type, exc
                )
            revision = PlanRevision(
                ts=now,
                ticker=tr.ticker,
                field=field,
                old=old,
                new=new if accepted else proposal.value,
                source="claude",
                reason=reason,
                accepted=accepted,
            )
            state.revisions.append(revision)
            (applied if accepted else rejected).append(revision)

        if ticker is not None and tr.event_class != "nessuno":
            state.journal.append(
                JournalEntry(
                    ts=now,
                    ticker=tr.ticker,
                    action=ticker.signal.action,
                    close=ticker.close,
                    reasons=[tr.summary],
                    source="claude",
                    event_class=tr.event_class,
                )
            )

    return FinalView(
        snapshot=view_snapshot,
        reviews=reviews,
        applied=applied,
        rejected=rejected,
        review_available=True,
        watched=sorted(watched),
        glossary=review.glossary,
        market_note=review.market_note,
    )


def _load_review(path: Path) -> ClaudeReview | None:
    """Legge la revisione; assente o non valida = None (modalità degradata, non un errore)."""
    if not path.exists():
        logger.warning("%s assente: pagina con i soli segnali del codice", path)
        return None
    try:
        return ClaudeReview.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError, OSError) as exc:
        logger.warning("%s non valido, revisione ignorata: %s", path, exc)
        return None


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    """Exit code 0 anche senza revisione (o con revisione non valida); 2 se mancano
    snapshot o stato validi."""
    parser = argparse.ArgumentParser(
        prog="python -m stocks.apply_review",
        description="Valida la revisione di Claude e scrive la vista finale.",
    )
    parser.add_argument(
        "--state", default="out/state.json", help="file JSON dello stato"
    )
    parser.add_argument(
        "--out", default="out", help="cartella con snapshot e revisione"
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    state_path = Path(args.state)
    now = now or datetime.now(PAGE_TZ)
    try:
        snapshot = Snapshot.model_validate_json(
            (out_dir / SNAPSHOT_FILE).read_text("utf-8")
        )
        state = load_state(state_path)
    except (OSError, ValueError) as exc:  # ValidationError è un ValueError
        logger.error("Snapshot o stato non leggibili: %s", exc)
        return 2

    view = apply(snapshot, _load_review(out_dir / REVIEW_FILE), state, now)
    if not view.review_available:
        logger.warning("Revisione notizie non disponibile")
    (out_dir / FINAL_FILE).write_text(view.model_dump_json(indent=2), encoding="utf-8")
    save_state(state, state_path)
    logger.info(
        "Revisione: %d modifiche ammesse, %d rifiutate",
        len(view.applied),
        len(view.rejected),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(main())
