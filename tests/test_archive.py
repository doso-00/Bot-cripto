from datetime import date, datetime

import pandas as pd
import pytest

from bot.data import archive
from tests.conftest import checksum_for, make_zip, raw_klines


def test_monthly_url_follows_archive_layout():
    assert archive.monthly_url("BTC/USDT", "1h", 2020, 1) == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2020-01.zip"
    )


def test_iter_months_crosses_year_boundary():
    assert list(archive.iter_months(date(2020, 11, 15), date(2021, 2, 1))) == [
        (2020, 11), (2020, 12), (2021, 1), (2021, 2)
    ]


@pytest.mark.parametrize(
    "today, expected",
    [(date(2026, 10, 23), date(2026, 9, 1)), (date(2026, 1, 1), date(2025, 12, 1)), (date(2024, 3, 31), date(2024, 2, 1))],
)
def test_last_complete_month(today, expected):
    assert archive.last_complete_month(today) == expected


class FakeArchive:
    """Simula data.binance.vision: mappa URL -> contenuto, 404 per il resto."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.files:
            raise archive.NotPublishedError(url)
        return self.files[url]


def archive_month(symbol, timeframe, year, month, rows, corrupt_checksum=False, header=False):
    url = archive.monthly_url(symbol, timeframe, year, month)
    name = url.rsplit("/", 1)[1]
    content = make_zip(rows, name.replace(".zip", ".csv"), header=header)
    checksum = checksum_for(b"altro" if corrupt_checksum else content, name)
    return {url: content, url + ".CHECKSUM": checksum}


def test_download_verifies_checksum_and_saves_file(tmp_path):
    fake = FakeArchive(archive_month("BTC/USDT", "1h", 2020, 1, raw_klines(datetime(2020, 1, 1), 744, "1h")))
    path = archive.download_month("BTC/USDT", "1h", 2020, 1, tmp_path, fetch=fake)
    assert path.exists() and path.name == "BTCUSDT-1h-2020-01.zip"
    frame = archive.read_month(path, "BTC/USDT", "1h")
    assert len(frame) == 744 and (frame["source"] == "bulk").all()


def test_wrong_checksum_raises_and_writes_nothing(tmp_path):
    fake = FakeArchive(archive_month("BTC/USDT", "1h", 2020, 1, raw_klines(datetime(2020, 1, 1), 10, "1h"), corrupt_checksum=True))
    with pytest.raises(archive.ChecksumError):
        archive.download_month("BTC/USDT", "1h", 2020, 1, tmp_path, fetch=fake)
    assert not list(tmp_path.rglob("*.zip"))


def test_existing_file_is_not_downloaded_again(tmp_path):
    fake = FakeArchive(archive_month("BTC/USDT", "1h", 2020, 1, raw_klines(datetime(2020, 1, 1), 10, "1h")))
    archive.download_month("BTC/USDT", "1h", 2020, 1, tmp_path, fetch=fake)
    n_calls = len(fake.calls)
    archive.download_month("BTC/USDT", "1h", 2020, 1, tmp_path, fetch=fake)
    assert len(fake.calls) == n_calls


def test_missing_month_raises_not_published(tmp_path):
    with pytest.raises(archive.NotPublishedError):
        archive.download_month("BTC/USDT", "1h", 2030, 1, tmp_path, fetch=FakeArchive({}))


def test_read_month_with_header_and_microseconds(tmp_path):
    files = archive_month("ETH/USDT", "4h", 2025, 1, raw_klines(datetime(2025, 1, 1), 6, "4h", micro=True), header=True)
    path = archive.download_month("ETH/USDT", "4h", 2025, 1, tmp_path, fetch=FakeArchive(files))
    frame = archive.read_month(path, "ETH/USDT", "4h")
    assert len(frame) == 6
    assert frame["open_time"].iat[1] == pd.Timestamp("2025-01-01 04:00:00")
