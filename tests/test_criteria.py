"""Criteri della Fase 3 con le definizioni fissate il 2026-10-23 (docs/DECISIONI.md)."""

from bot.evaluate.criteria import beats_benchmark, phase3_criteria


def m(ret, dd=0.3, trades=40):
    return {"total_return": ret, "max_drawdown": dd, "n_trades": trades}


def test_higher_return_beats_benchmark():
    assert beats_benchmark(m(0.50), m(0.40, dd=0.2))[0]


def test_similar_return_needs_30_percent_lower_drawdown():
    assert beats_benchmark(m(0.36, dd=0.35), m(0.40, dd=0.50))[0]          # -4 punti, drawdown -30%
    assert not beats_benchmark(m(0.36, dd=0.36), m(0.40, dd=0.50))[0]      # drawdown solo -28%
    assert not beats_benchmark(m(0.34, dd=0.10), m(0.40, dd=0.50))[0]      # -6 punti: non è "simile"


def test_all_four_criteria():
    good = phase3_criteria(m(0.5), m(0.4), m(0.45), m(0.3), m(0.2), m(0.25), "ETH/USDT")
    assert [c.passed for c in good] == [True, True, True, True]

    few_trades = phase3_criteria(m(0.5, trades=29), m(0.4), m(0.45), m(0.3), m(0.2), m(0.25), "ETH/USDT")
    assert [c.passed for c in few_trades] == [True, False, True, True]

    negative_with_slippage = phase3_criteria(m(0.5), m(0.4), m(-0.01), m(0.3), m(0.2), m(0.25), "ETH/USDT")
    assert not negative_with_slippage[2].passed

    eth_fails = phase3_criteria(m(0.5), m(0.4), m(0.45), m(0.1), m(0.4, dd=0.3), m(0.05), "ETH/USDT")
    assert not eth_fails[3].passed and "ETH/USDT" in eth_fails[3].name
