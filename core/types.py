"""Chanlun analysis constants and enumerations."""


class ChanlunDirection:
    UP = "up"
    DOWN = "down"
    NONE = "none"


class FractalType:
    TOP = "top"
    BOTTOM = "bottom"


class BuySellType:
    BUY1 = "buy1"
    BUY2 = "buy2"
    BUY3 = "buy3"
    SELL1 = "sell1"
    SELL2 = "sell2"
    SELL3 = "sell3"
    SEMI_BUY2 = "semiBuy2"
    SEMI_BUY3 = "semiBuy3"
    SEMI_SELL2 = "semiSell2"
    SEMI_SELL3 = "semiSell3"


BUYSELL_LABELS = {
    "buy1": "一买",
    "buy2": "二买",
    "buy3": "三买",
    "sell1": "一卖",
    "sell2": "二卖",
    "sell3": "三卖",
    "semiBuy2": "类二买",
    "semiBuy3": "类三买",
    "semiSell2": "类二卖",
    "semiSell3": "类三卖",
}


BUYSELL_DESCRIPTIONS = {
    "buy1": {
        "name": "第一类买点（一买）",
        "principle": "下跌趋势中，最后一个中枢之下出现底背驰，即价格创新低但MACD面积/力度不创新低。",
        "theory": "缠论认为趋势由至少两个同级别中枢组成。当最后一段下跌的力度不及前一段，说明下跌动能衰竭，趋势即将反转。",
        "analysis": "一买是最具价值但风险也最大的买点，出现在趋势的绝对底部区域。确认背驰后入场，止损设在新低之下。",
    },
    "buy2": {
        "name": "第二类买点（二买）",
        "principle": "一买出现后价格回升，随后再次回落但不跌破一买的低点，形成更高的低点。",
        "theory": "一买确认趋势反转后，市场会有一次回踩确认。二买是对趋势反转的再次确认，不破一买低点说明空方已无力再创新低。",
        "analysis": "二买是最安全的买点之一，成功率高。回踩不破一买低点即确认多头趋势成立，适合稳健型交易者。",
    },
    "buy3": {
        "name": "第三类买点（三买）",
        "principle": "离开中枢后向下回拉不再回到中枢内（不跌破中枢上沿ZG），形成向上突破确认。",
        "theory": "价格向上突破中枢后回踩，如果回踩不进中枢区间，说明中枢已被有效突破，新的上升趋势或更大级别中枢将形成。",
        "analysis": "三买是趋势延续的确认信号。中枢突破后回踩确认，是加仓或追涨的好时机，止损设在中枢上沿。",
    },
    "sell1": {
        "name": "第一类卖点（一卖）",
        "principle": "上涨趋势中，最后一个中枢之上出现顶背驰，即价格创新高但MACD面积/力度不创新高。",
        "theory": "当最后一段上涨的力度不及前一段，说明上涨动能衰竭。多方力量已尽，趋势即将由上转下。",
        "analysis": "一卖是最佳卖出时机，出现在趋势顶部。确认顶背驰后应果断减仓，是左侧交易的典型信号。",
    },
    "sell2": {
        "name": "第二类卖点（二卖）",
        "principle": "一卖出现后价格下跌，随后反弹但不超过一卖的高点，形成更低的高点。",
        "theory": "一卖确认趋势反转后，市场会有一次反弹。二卖是对下跌趋势的二次确认，不破一卖高点说明多方已无力再创新高。",
        "analysis": "二卖是错过一卖后的第二次离场机会，确定性更高。反弹不破前高即确认空头趋势，适合作为清仓信号。",
    },
    "sell3": {
        "name": "第三类卖点（三卖）",
        "principle": "离开中枢后向上反弹不再回到中枢内（不突破中枢下沿ZD），形成向下突破确认。",
        "theory": "价格向下跌破中枢后反弹，如果反弹不进中枢区间，说明中枢已被有效跌破，新的下降趋势或更大级别中枢将形成。",
        "analysis": "三卖是下跌趋势延续的确认信号。中枢跌破后反弹确认，是做空或减仓的明确信号，止损设在中枢下沿。",
    },
    "semiBuy2": {
        "name": "类二买",
        "principle": "在中枢震荡中，价格触碰中枢下沿附近不破前低，类似二买结构。",
        "theory": "虽然不满足严格的二买条件（需趋势反转后回踩），但形态相似，在中枢下沿获得支撑，有一定参考价值。",
        "analysis": "类二买信号强度弱于标准二买，需要结合中枢位置和量价关系综合判断，建议轻仓参与。",
    },
    "semiBuy3": {
        "name": "类三买",
        "principle": "回拉触碰中枢上沿附近获得支撑，类似三买结构但未完全远离中枢。",
        "theory": "价格在中枢上方运行，回踩至中枢上沿附近即获支撑，显示多方仍有优势，但突破力度不如标准三买。",
        "analysis": "类三买参考价值略低于标准三买，适合在趋势明确时作为辅助买入参考。",
    },
    "semiSell2": {
        "name": "类二卖",
        "principle": "在中枢震荡中，价格触碰中枢上沿附近不破前高，类似二卖结构。",
        "theory": "虽然不满足严格的二卖条件，但形态相似，在中枢上沿遇阻回落，有一定参考价值。",
        "analysis": "类二卖信号强度弱于标准二卖，需结合其他信号综合判断，建议仅作为减仓参考。",
    },
    "semiSell3": {
        "name": "类三卖",
        "principle": "反弹触碰中枢下沿附近遇阻回落，类似三卖结构但未完全远离中枢。",
        "theory": "价格在中枢下方运行，反弹至中枢下沿附近即受阻，显示空方仍有优势，但下跌力度不如标准三卖。",
        "analysis": "类三卖参考价值略低于标准三卖，适合在下跌趋势中作为辅助卖出参考。",
    },
}


class MovementType:
    TREND_UP = "趋势上涨"
    TREND_DOWN = "趋势下跌"
    CONSOLIDATION_UP = "盘整上涨"
    CONSOLIDATION_DOWN = "盘整下跌"
    CONSOLIDATION = "盘整"


class DivergenceType:
    TREND = "trend"
    CONSOLIDATION = "consolidation"


class SegmentEndType:
    CHAR_SEQUENCE = "charSequence"
    BI_BREAK = "biBreak"


class HubType:
    STANDARD = "standard"
    EXTENDED = "extended"
    UPGRADED = "upgraded"
