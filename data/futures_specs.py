"""Futures contract specifications and industry classification.

Ported from renderer/js/data/futures-specs.js.
"""

from __future__ import annotations

from datetime import date, timedelta


FUTURES_CONTRACT_SPECS: dict[str, dict] = {
    # ==================== DCE ====================
    "C":   {"name": "玉米",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1.2,  "dominantMonths": [1, 5, 9]},
    "CS":  {"name": "淀粉",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1.5,  "dominantMonths": [1, 5, 9]},
    "M":   {"name": "豆粕",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1.5,  "dominantMonths": [1, 5, 9]},
    "A":   {"name": "豆一",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "B":   {"name": "豆二",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1,    "dominantMonths": [1, 5, 9]},
    "Y":   {"name": "豆油",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 2,   "tickValue": 20,   "commission": 2.5,  "dominantMonths": [1, 5, 9]},
    "P":   {"name": "棕榈油",   "exchange": "DCE",  "multiplier": 10,  "tickSize": 2,   "tickValue": 20,   "commission": 2.5,  "dominantMonths": [1, 5, 9]},
    "JD":  {"name": "鸡蛋",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1.5,  "dominantMonths": [1, 5, 9]},
    "LH":  {"name": "生猪",     "exchange": "DCE",  "multiplier": 16,  "tickSize": 5,   "tickValue": 80,   "commission": 4,    "dominantMonths": [1, 5, 9, 11]},
    "I":   {"name": "铁矿石",   "exchange": "DCE",  "multiplier": 100, "tickSize": 0.5, "tickValue": 50,   "commission": 3.5,  "dominantMonths": [1, 5, 9]},
    "J":   {"name": "焦炭",     "exchange": "DCE",  "multiplier": 100, "tickSize": 0.5, "tickValue": 50,   "commission": 3.6,  "dominantMonths": [1, 5, 9]},
    "JM":  {"name": "焦煤",     "exchange": "DCE",  "multiplier": 60,  "tickSize": 0.5, "tickValue": 30,   "commission": 3.6,  "dominantMonths": [1, 5, 9]},
    "L":   {"name": "塑料",     "exchange": "DCE",  "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 1,    "dominantMonths": [1, 5, 9]},
    "V":   {"name": "PVC",      "exchange": "DCE",  "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 1,    "dominantMonths": [1, 5, 9]},
    "PP":  {"name": "聚丙烯",   "exchange": "DCE",  "multiplier": 5,   "tickSize": 1,   "tickValue": 5,    "commission": 1,    "dominantMonths": [1, 5, 9]},
    "EG":  {"name": "乙二醇",   "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "EB":  {"name": "苯乙烯",   "exchange": "DCE",  "multiplier": 5,   "tickSize": 1,   "tickValue": 5,    "commission": 3,    "dominantMonths": [1, 5, 9]},
    "PG":  {"name": "LPG",      "exchange": "DCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "RR":  {"name": "粳米",     "exchange": "DCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1,    "dominantMonths": [1, 5, 9]},
    "FB":  {"name": "纤维板",   "exchange": "DCE",  "multiplier": 10,  "tickSize": 0.5, "tickValue": 5,    "commission": 1,    "dominantMonths": [1, 5, 9]},
    "BB":  {"name": "胶合板",   "exchange": "DCE",  "multiplier": 500, "tickSize": 0.05,"tickValue": 25,   "commission": 1,    "dominantMonths": [1, 5, 9]},
    "SS":  {"name": "不锈钢",   "exchange": "SHFE", "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    # ==================== SHFE ====================
    "CU":  {"name": "铜",       "exchange": "SHFE", "multiplier": 5,   "tickSize": 10,  "tickValue": 50,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "AL":  {"name": "铝",       "exchange": "SHFE", "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "ZN":  {"name": "锌",       "exchange": "SHFE", "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "PB":  {"name": "铅",       "exchange": "SHFE", "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "NI":  {"name": "镍",       "exchange": "SHFE", "multiplier": 1,   "tickSize": 10,  "tickValue": 10,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "SN":  {"name": "锡",       "exchange": "SHFE", "multiplier": 1,   "tickSize": 10,  "tickValue": 10,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "AU":  {"name": "黄金",     "exchange": "SHFE", "multiplier": 1000,"tickSize": 0.02,"tickValue": 20,   "commission": 10,   "dominantMonths": [6, 12]},
    "AG":  {"name": "白银",     "exchange": "SHFE", "multiplier": 15,  "tickSize": 1,   "tickValue": 15,   "commission": 3.5,  "dominantMonths": [6, 12]},
    "RB":  {"name": "螺纹钢",   "exchange": "SHFE", "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 3.5,  "dominantMonths": [1, 5, 10]},
    "HC":  {"name": "热卷",     "exchange": "SHFE", "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 3.5,  "dominantMonths": [1, 5, 10]},
    "WR":  {"name": "线材",     "exchange": "SHFE", "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 10]},
    "RU":  {"name": "橡胶",     "exchange": "SHFE", "multiplier": 10,  "tickSize": 5,   "tickValue": 50,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "BU":  {"name": "沥青",     "exchange": "SHFE", "multiplier": 10,  "tickSize": 2,   "tickValue": 20,   "commission": 3,    "dominantMonths": [6, 12]},
    "FU":  {"name": "燃料油",   "exchange": "SHFE", "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "SP":  {"name": "纸浆",     "exchange": "SHFE", "multiplier": 10,  "tickSize": 2,   "tickValue": 20,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "AO":  {"name": "氧化铝",   "exchange": "SHFE", "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "BR":  {"name": "丁二烯橡胶","exchange": "SHFE", "multiplier": 5,  "tickSize": 5,   "tickValue": 25,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    # ==================== INE ====================
    "SC":  {"name": "原油",     "exchange": "INE",  "multiplier": 1000,"tickSize": 0.1, "tickValue": 100,  "commission": 20,   "dominantMonths": list(range(1, 13))},
    "LU":  {"name": "低硫燃料油","exchange": "INE",  "multiplier": 10, "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "NR":  {"name": "20号胶",   "exchange": "INE",  "multiplier": 10,  "tickSize": 5,   "tickValue": 50,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "BC":  {"name": "国际铜",   "exchange": "INE",  "multiplier": 5,   "tickSize": 10,  "tickValue": 50,   "commission": 3,    "dominantMonths": list(range(1, 13))},
    "EC":  {"name": "集运指数",  "exchange": "INE",  "multiplier": 50,  "tickSize": 0.1, "tickValue": 5,    "commission": 4,    "dominantMonths": [2, 4, 6, 8, 10, 12]},
    # ==================== ZCE ====================
    "CF":  {"name": "棉花",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 4.3,  "dominantMonths": [1, 5, 9]},
    "SR":  {"name": "白糖",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "TA":  {"name": "PTA",      "exchange": "ZCE",  "multiplier": 5,   "tickSize": 2,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "MA":  {"name": "甲醇",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "OI":  {"name": "菜油",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "RM":  {"name": "菜粕",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 1.5,  "dominantMonths": [1, 5, 9]},
    "FG":  {"name": "玻璃",     "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "SA":  {"name": "纯碱",     "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 3.5,  "dominantMonths": [1, 5, 9]},
    "UR":  {"name": "尿素",     "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 5,    "dominantMonths": [1, 5, 9]},
    "AP":  {"name": "苹果",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 5,    "dominantMonths": [1, 5, 10]},
    "CJ":  {"name": "红枣",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 5,   "tickValue": 25,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "PK":  {"name": "花生",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 2,   "tickValue": 10,   "commission": 4,    "dominantMonths": [1, 3, 10]},
    "SF":  {"name": "硅铁",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 2,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "SM":  {"name": "锰硅",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 2,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "ZC":  {"name": "动力煤",   "exchange": "ZCE",  "multiplier": 100, "tickSize": 0.2, "tickValue": 20,   "commission": 4,    "dominantMonths": [1, 5, 9]},
    "WH":  {"name": "强麦",     "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "PM":  {"name": "普麦",     "exchange": "ZCE",  "multiplier": 50,  "tickSize": 1,   "tickValue": 50,   "commission": 5,    "dominantMonths": [1, 5, 9]},
    "RI":  {"name": "早籼稻",   "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 2.5,  "dominantMonths": [1, 5, 9]},
    "LR":  {"name": "晚籼稻",   "exchange": "ZCE",  "multiplier": 20,  "tickSize": 1,   "tickValue": 20,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "RS":  {"name": "菜籽",     "exchange": "ZCE",  "multiplier": 10,  "tickSize": 1,   "tickValue": 10,   "commission": 2,    "dominantMonths": [1, 5, 9]},
    "PF":  {"name": "短纤",     "exchange": "ZCE",  "multiplier": 5,   "tickSize": 2,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "SH":  {"name": "烧碱",     "exchange": "ZCE",  "multiplier": 30,  "tickSize": 1,   "tickValue": 30,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    "PX":  {"name": "对二甲苯",  "exchange": "ZCE",  "multiplier": 5,  "tickSize": 2,   "tickValue": 10,   "commission": 3,    "dominantMonths": [1, 5, 9]},
    # ==================== CFFEX ====================
    "IF":  {"name": "沪深300",  "exchange": "CFFEX", "multiplier": 300, "tickSize": 0.2, "tickValue": 60,   "commission": 23,   "dominantMonths": [3, 6, 9, 12]},
    "IH":  {"name": "上证50",   "exchange": "CFFEX", "multiplier": 300, "tickSize": 0.2, "tickValue": 60,   "commission": 23,   "dominantMonths": [3, 6, 9, 12]},
    "IC":  {"name": "中证500",  "exchange": "CFFEX", "multiplier": 200, "tickSize": 0.2, "tickValue": 40,   "commission": 23,   "dominantMonths": [3, 6, 9, 12]},
    "IM":  {"name": "中证1000", "exchange": "CFFEX", "multiplier": 200, "tickSize": 0.2, "tickValue": 40,   "commission": 23,   "dominantMonths": [3, 6, 9, 12]},
    "T":   {"name": "十年国债",  "exchange": "CFFEX", "multiplier": 10000, "tickSize": 0.005, "tickValue": 50, "commission": 3, "dominantMonths": [3, 6, 9, 12]},
    "TF":  {"name": "五年国债",  "exchange": "CFFEX", "multiplier": 10000, "tickSize": 0.005, "tickValue": 50, "commission": 3, "dominantMonths": [3, 6, 9, 12]},
    "TS":  {"name": "两年国债",  "exchange": "CFFEX", "multiplier": 20000, "tickSize": 0.005, "tickValue": 100, "commission": 3, "dominantMonths": [3, 6, 9, 12]},
    "TL":  {"name": "三十年国债","exchange": "CFFEX", "multiplier": 10000, "tickSize": 0.01, "tickValue": 100, "commission": 3, "dominantMonths": [3, 6, 9, 12]},
}

FUTURES_INDUSTRY_MAP: dict[str, list[str]] = {
    "农产品": ["C", "CS", "M", "A", "B", "Y", "P", "JD", "LH", "CF", "SR", "OI", "RM", "AP", "CJ", "PK", "WH", "PM", "RI", "LR", "RS", "RR"],
    "黑色系": ["I", "J", "JM", "RB", "HC", "WR", "SF", "SM", "SS"],
    "有色金属": ["CU", "AL", "ZN", "PB", "NI", "SN", "BC", "AO"],
    "能源化工": ["SC", "FU", "BU", "LU", "NR", "MA", "TA", "L", "V", "PP", "EG", "EB", "PG", "SA", "FG", "UR", "RU", "SP", "ZC", "PF", "SH", "PX", "BR"],
    "贵金属": ["AU", "AG"],
    "金融期货": ["IF", "IH", "IC", "IM", "T", "TF", "TS", "TL"],
    "航运": ["EC"],
}


def get_spec_by_prefix(prefix: str) -> dict | None:
    return FUTURES_CONTRACT_SPECS.get(prefix)


def get_industry_by_prefix(prefix: str) -> str:
    for industry, prefixes in FUTURES_INDUSTRY_MAP.items():
        if prefix in prefixes:
            return industry
    return "其他"


def get_industry_list() -> list[str]:
    return list(FUTURES_INDUSTRY_MAP.keys())


def get_varieties_by_industry(industry: str) -> list[dict]:
    prefixes = FUTURES_INDUSTRY_MAP.get(industry, [])
    result = []
    for p in prefixes:
        spec = FUTURES_CONTRACT_SPECS.get(p)
        if spec:
            result.append({"prefix": p, **spec})
        else:
            result.append({"prefix": p, "name": p, "exchange": "UNKNOWN"})
    return result


def get_dominant_contract(prefix: str, exchange: str, year: int, month: int,
                          dominant_months: list[int]) -> str:
    if not dominant_months:
        ym = f"{year % 100:02d}{month:02d}"
        return f"{prefix}{ym}.{exchange}"

    if len(dominant_months) == 12:
        nxt = month + 1 if month < 12 else 1
        y = year if month < 12 else year + 1
        ym = f"{y % 100:02d}{nxt:02d}"
        return f"{prefix}{ym}.{exchange}"

    sorted_months = sorted(dominant_months)
    for dm in sorted_months:
        if dm > month:
            ym = f"{year % 100:02d}{dm:02d}"
            return f"{prefix}{ym}.{exchange}"

    dm = sorted_months[0]
    ym = f"{(year + 1) % 100:02d}{dm:02d}"
    return f"{prefix}{ym}.{exchange}"


def _next_switch_date(year: int, month: int, dominant_months: list[int]) -> date:
    sorted_months = sorted(dominant_months)
    for dm in sorted_months:
        if dm > month:
            return date(year, dm, 1)
    return date(year + 1, sorted_months[0], 1)


def build_dominant_segments(prefix: str, exchange: str, start_date: date,
                            end_date: date,
                            dominant_months: list[int]) -> list[dict]:
    if not dominant_months:
        return []

    segments: list[dict] = []
    cursor = start_date

    while cursor <= end_date:
        ts_code = get_dominant_contract(prefix, exchange, cursor.year, cursor.month, dominant_months)
        seg_end = _next_switch_date(cursor.year, cursor.month, dominant_months)
        actual_end = end_date if seg_end > end_date else seg_end - timedelta(days=1)

        if segments and segments[-1]["tsCode"] == ts_code:
            segments[-1]["end"] = actual_end
        else:
            segments.append({"tsCode": ts_code, "start": cursor, "end": actual_end})

        cursor = seg_end

    return segments
