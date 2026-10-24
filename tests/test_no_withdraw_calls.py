"""Test obbligatorio del brief §10: nessuna chiamata a prelievi o trasferimenti nel codice.

La ricerca è limitata a bot/ perché la libreria ccxt contiene (legittimamente) metodi di
prelievo: il punto è che il nostro codice non li chiami mai. Si escludono commenti e
docstring (possono citare i divieti), ma non le stringhe nel codice: un
`getattr(ex, "withdraw")` verrebbe trovato.
"""

import ast
import io
import re
import tokenize
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent / "bot"

FORBIDDEN = re.compile(
    # "margin" da solo darebbe falsi positivi sul CSS: i metodi ccxt di margin contengono _margin o margin_
    r"withdraw|transfer|sapi|_margin|margin_|borrow|repay|futures|fapi|dapi|leverage|staking|_convert|convert_",
    re.IGNORECASE,
)


def code_lines(source: str) -> dict[int, str]:
    """Righe di codice senza commenti e senza docstring, indicizzate per numero di riga."""
    docstring_lines: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            docstring_lines.update(range(body[0].lineno, body[0].end_lineno + 1))

    lines = source.splitlines()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            row, col = tok.start
            lines[row - 1] = lines[row - 1][:col]
    return {n: line for n, line in enumerate(lines, start=1) if n not in docstring_lines}


def find_forbidden(source: str) -> list[tuple[int, str]]:
    return [(n, line.strip()) for n, line in code_lines(source).items() if FORBIDDEN.search(line)]


def test_no_withdraw_or_transfer_calls_in_bot_code():
    offenders = [
        f"{path.relative_to(BOT_DIR.parent)}:{n}: {line}"
        for path in sorted(BOT_DIR.rglob("*.py"))
        for n, line in find_forbidden(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, "chiamate vietate trovate:\n" + "\n".join(offenders)


def test_scanner_catches_calls_and_ignores_comments():
    source = '"""Mai chiamare withdraw."""\n# niente transfer\nx = 1\nex.withdraw("BTC", 1)\ngetattr(ex, "sapi_post_x")\n'
    assert [n for n, _ in find_forbidden(source)] == [4, 5]


def test_scanner_flags_margin_methods_but_not_css():
    source = 'css = "body { margin:0 }"\nlayout = dict(margin={"l": 0})\nex.borrow_margin("USDT", 1)\nex.set_margin_mode("cross")\n'
    assert [n for n, _ in find_forbidden(source)] == [3, 4]
