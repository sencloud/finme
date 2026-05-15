"""MACD calculation for divergence detection."""

from __future__ import annotations


def calculate_macd(klines: list[dict], fast: int = 12, slow: int = 26,
                   signal: int = 9) -> dict:
    """Calculate MACD (DIF/DEA/histogram) from K-line close prices."""
    closes = [k["close"] for k in klines]

    def ema(data: list[float], period: int) -> list[float]:
        result: list[float] = []
        multiplier = 2.0 / (period + 1)
        for i, val in enumerate(data):
            if i == 0:
                result.append(val)
            else:
                result.append((val - result[i - 1]) * multiplier + result[i - 1])
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    dif: list[float | None] = []
    for i in range(len(closes)):
        if i < slow - 1:
            dif.append(None)
        else:
            dif.append(ema_fast[i] - ema_slow[i])

    dea: list[float | None] = []
    dea_ema: float | None = None
    mult = 2.0 / (signal + 1)
    for i in range(len(closes)):
        if dif[i] is None:
            dea.append(None)
        elif dea_ema is None:
            dea_ema = dif[i]
            dea.append(dea_ema)
        else:
            dea_ema = (dif[i] - dea_ema) * mult + dea_ema
            dea.append(dea_ema)

    histogram: list[float | None] = []
    for i in range(len(closes)):
        if dif[i] is None or dea[i] is None:
            histogram.append(None)
        else:
            histogram.append((dif[i] - dea[i]) * 2)

    return {"dif": dif, "dea": dea, "histogram": histogram}
