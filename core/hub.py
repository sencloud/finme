"""Hub (中枢) identification and overlapping hub upgrade.

A hub is formed by at least 3 bi with overlapping price ranges:
  ZG = min(each bi's high)  -- hub upper edge
  ZD = max(each bi's low)   -- hub lower edge
  Valid when ZG > ZD.

Extended hub: > 9 bi (prolonged same-level oscillation).
Overlapping hubs are recursively merged into higher-level hubs.
"""

from __future__ import annotations

import math


def find_hubs(bis: list[dict]) -> list[dict]:
    """Identify hubs from a bi sequence."""
    if not bis or len(bis) < 3:
        return []

    hubs: list[dict] = []
    i = 0

    while i < len(bis) - 2:
        b1 = bis[i]
        b2 = bis[i + 1]
        b3 = bis[i + 2]

        zg = min(b1["high"], b2["high"], b3["high"])
        zd = max(b1["low"], b2["low"], b3["low"])

        if zg <= zd:
            i += 1
            continue

        hub_bis = [b1, b2, b3]
        end_idx = i + 2

        for j in range(i + 3, len(bis)):
            bj = bis[j]
            if bj["high"] > zd and bj["low"] < zg:
                hub_bis.append(bj)
                end_idx = j
            else:
                break

        gg = max(b["high"] for b in hub_bis)
        dd = min(b["low"] for b in hub_bis)
        entry_direction = hub_bis[0]["direction"]
        exit_direction = hub_bis[-1]["direction"]
        is_extended = len(hub_bis) > 9

        hubs.append({
            "ZG": zg,
            "ZD": zd,
            "GG": gg,
            "DD": dd,
            "startIndex": hub_bis[0]["startFractal"]["klineIndex"],
            "endIndex": hub_bis[-1]["endFractal"]["klineIndex"],
            "startFractal": hub_bis[0]["startFractal"],
            "endFractal": hub_bis[-1]["endFractal"],
            "bis": hub_bis,
            "level": 1,
            "entryDirection": entry_direction,
            "exitDirection": exit_direction,
            "extended": is_extended,
            "hubType": "extended" if is_extended else "standard",
        })

        i = end_idx + 1

    return hubs


def upgrade_overlapping_hubs(hubs: list[dict]) -> list[dict]:
    """Recursively merge overlapping hubs into higher-level hubs."""
    if not hubs or len(hubs) < 2:
        return hubs

    result: list[dict] = []
    current = dict(hubs[0])

    for i in range(1, len(hubs)):
        nxt = hubs[i]
        overlaps = current["ZG"] > nxt["ZD"] and nxt["ZG"] > current["ZD"]

        if overlaps:
            current = {
                "ZG": min(current["ZG"], nxt["ZG"]),
                "ZD": max(current["ZD"], nxt["ZD"]),
                "GG": max(current["GG"], nxt["GG"]),
                "DD": min(current["DD"], nxt["DD"]),
                "startIndex": current["startIndex"],
                "endIndex": nxt["endIndex"],
                "startFractal": current["startFractal"],
                "endFractal": nxt["endFractal"],
                "bis": current["bis"] + nxt["bis"],
                "level": max(current["level"], nxt["level"]) + 1,
                "entryDirection": current["entryDirection"],
                "exitDirection": nxt["exitDirection"],
                "extended": False,
                "hubType": "upgraded",
                "sourceHubs": current.get("sourceHubs", [current]) + [nxt],
            }
        else:
            result.append(current)
            current = dict(nxt)

    result.append(current)

    if len(result) < len(hubs):
        return upgrade_overlapping_hubs(result)
    return result
