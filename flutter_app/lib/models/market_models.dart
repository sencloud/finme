typedef JsonMap = Map<String, dynamic>;

const buySellLabels = {
  'buy1': '一买',
  'buy2': '二买',
  'buy3': '三买',
  'sell1': '一卖',
  'sell2': '二卖',
  'sell3': '三卖',
  'semiBuy2': '类二买',
  'semiBuy3': '类三买',
  'semiSell2': '类二卖',
  'semiSell3': '类三卖',
};

JsonMap asJsonMap(Object? value) {
  if (value is Map<String, dynamic>) {
    return value;
  }
  if (value is Map) {
    return value.map((key, item) => MapEntry(key.toString(), item));
  }
  return {};
}

List<JsonMap> asJsonMapList(Object? value) {
  if (value is! List) {
    return const [];
  }
  return value.map(asJsonMap).where((item) => item.isNotEmpty).toList();
}

double asDouble(Object? value, [double defaultValue = 0]) {
  if (value is num) {
    return value.toDouble();
  }
  if (value is String) {
    return double.tryParse(value) ?? defaultValue;
  }
  return defaultValue;
}

int asInt(Object? value, [int defaultValue = 0]) {
  if (value is num) {
    return value.toInt();
  }
  if (value is String) {
    return int.tryParse(value) ?? defaultValue;
  }
  return defaultValue;
}

bool asBool(Object? value, [bool defaultValue = false]) {
  if (value is bool) {
    return value;
  }
  if (value is num) {
    return value != 0;
  }
  if (value is String) {
    final lowered = value.toLowerCase();
    if (lowered == 'true') {
      return true;
    }
    if (lowered == 'false') {
      return false;
    }
  }
  return defaultValue;
}

String asText(Object? value, [String defaultValue = '']) {
  if (value == null) {
    return defaultValue;
  }
  final text = value.toString();
  return text.isEmpty ? defaultValue : text;
}

/// 市场类型 — 期货 vs A 股。
/// 后端 ScanService 按这个字段分发到不同的扫描分支。
enum MarketType {
  futures,
  stock;

  String get id => name; // 'futures' / 'stock'
  String get label => this == futures ? '期货' : 'A股';

  static MarketType parse(String? value) {
    if (value == null) return MarketType.futures;
    final v = value.toLowerCase();
    if (v == 'stock') return MarketType.stock;
    return MarketType.futures;
  }
}

class WatchItem {
  const WatchItem({
    required this.market,
    required this.prefix,
    required this.exchange,
    required this.name,
  });

  final MarketType market;
  final String prefix;
  final String exchange;
  final String name;

  /// 唯一标识 — market 不同也算不同品种(同一个 prefix 在期货/股票里都可能存在)。
  String get key => '${market.id}:$prefix.$exchange';

  /// 给后端展示用的代码 — 期货是 "C.DCE",股票是 "600519.SH"。
  String get displayCode => '$prefix.$exchange';

  WatchItem copyWith({
    MarketType? market,
    String? prefix,
    String? exchange,
    String? name,
  }) {
    return WatchItem(
      market: market ?? this.market,
      prefix: prefix ?? this.prefix,
      exchange: exchange ?? this.exchange,
      name: name ?? this.name,
    );
  }

  JsonMap toJson() => {
        'market': market.id,
        'prefix': prefix,
        'exchange': exchange,
        'name': name,
      };

  factory WatchItem.fromJson(JsonMap json) {
    return WatchItem(
      market: MarketType.parse(asText(json['market'], 'futures')),
      prefix: asText(json['prefix']),
      exchange: asText(json['exchange']),
      name: asText(json['name']),
    );
  }
}

class WatchlistResponse {
  const WatchlistResponse({
    required this.watchlist,
    required this.scanIntervalMinutes,
    required this.strategyPreset,
    required this.entryTimeframe,
  });

  final List<WatchItem> watchlist;
  final int scanIntervalMinutes;
  final String strategyPreset;
  final String entryTimeframe;

  factory WatchlistResponse.fromJson(JsonMap json) {
    return WatchlistResponse(
      watchlist:
          asJsonMapList(json['watchlist']).map(WatchItem.fromJson).toList(),
      scanIntervalMinutes: asInt(json['scan_interval_minutes']),
      strategyPreset: asText(json['strategy_preset']),
      entryTimeframe: asText(json['entry_timeframe']),
    );
  }
}

class Kline {
  const Kline({
    required this.time,
    required this.date,
    required this.open,
    required this.high,
    required this.low,
    required this.close,
    required this.volume,
  });

  final int time;
  final String date;
  final double open;
  final double high;
  final double low;
  final double close;
  final double volume;

  bool get isUp => close >= open;

  factory Kline.fromJson(JsonMap json) {
    return Kline(
      time: asInt(json['time']),
      date: asText(json['date']),
      open: asDouble(json['open']),
      high: asDouble(json['high']),
      low: asDouble(json['low']),
      close: asDouble(json['close']),
      volume: asDouble(json['volume']),
    );
  }
}

class Fractal {
  const Fractal({
    required this.type,
    required this.index,
    required this.klineIndex,
    required this.high,
    required this.low,
    required this.date,
  });

  final String type;
  final int index;
  final int klineIndex;
  final double high;
  final double low;
  final String date;

  factory Fractal.fromJson(JsonMap json) {
    return Fractal(
      type: asText(json['type']),
      index: asInt(json['index']),
      klineIndex: asInt(json['klineIndex']),
      high: asDouble(json['high']),
      low: asDouble(json['low']),
      date: asText(json['date']),
    );
  }
}

class Bi {
  const Bi({
    required this.startFractal,
    required this.endFractal,
    required this.direction,
    required this.high,
    required this.low,
    required this.finished,
  });

  final Fractal startFractal;
  final Fractal endFractal;
  final String direction;
  final double high;
  final double low;
  final bool finished;

  factory Bi.fromJson(JsonMap json) {
    return Bi(
      startFractal: Fractal.fromJson(asJsonMap(json['startFractal'])),
      endFractal: Fractal.fromJson(asJsonMap(json['endFractal'])),
      direction: asText(json['direction']),
      high: asDouble(json['high']),
      low: asDouble(json['low']),
      finished: asBool(json['finished'], true),
    );
  }
}

class Hub {
  const Hub({
    required this.zg,
    required this.zd,
    required this.gg,
    required this.dd,
    required this.startIndex,
    required this.endIndex,
    required this.level,
    required this.hubType,
  });

  final double zg;
  final double zd;
  final double gg;
  final double dd;
  final int startIndex;
  final int endIndex;
  final int level;
  final String hubType;

  factory Hub.fromJson(JsonMap json) {
    return Hub(
      zg: asDouble(json['ZG']),
      zd: asDouble(json['ZD']),
      gg: asDouble(json['GG']),
      dd: asDouble(json['DD']),
      startIndex: asInt(json['startIndex']),
      endIndex: asInt(json['endIndex']),
      level: asInt(json['level'], 1),
      hubType: asText(json['hubType'], 'standard'),
    );
  }
}

class BuySellPoint {
  const BuySellPoint({
    required this.type,
    required this.divergenceType,
    required this.price,
    required this.date,
    required this.index,
    required this.description,
    required this.biFinished,
  });

  final String type;
  final String divergenceType;
  final double price;
  final String date;
  final int index;
  final String description;
  final bool biFinished;

  String get label => buySellLabels[type] ?? type;
  bool get isBuy => type.toLowerCase().contains('buy');

  factory BuySellPoint.fromJson(JsonMap json) {
    return BuySellPoint(
      type: asText(json['type']),
      divergenceType: asText(json['divergenceType']),
      price: asDouble(json['price']),
      date: asText(json['date']),
      index: asInt(json['index']),
      description: asText(json['description']),
      biFinished: asBool(json['biFinished'], true),
    );
  }
}

class ChanlunResult {
  const ChanlunResult({
    required this.mergedKlines,
    required this.fractals,
    required this.bis,
    required this.hubs,
    required this.buySellPoints,
    required this.currentTrend,
    required this.movementType,
    required this.duration,
  });

  final List<Kline> mergedKlines;
  final List<Fractal> fractals;
  final List<Bi> bis;
  final List<Hub> hubs;
  final List<BuySellPoint> buySellPoints;
  final String currentTrend;
  final String movementType;
  final int duration;

  factory ChanlunResult.fromJson(JsonMap json) {
    return ChanlunResult(
      mergedKlines:
          asJsonMapList(json['mergedKlines']).map(Kline.fromJson).toList(),
      fractals: asJsonMapList(json['fractals']).map(Fractal.fromJson).toList(),
      bis: asJsonMapList(json['bis']).map(Bi.fromJson).toList(),
      hubs: asJsonMapList(json['hubs']).map(Hub.fromJson).toList(),
      buySellPoints: asJsonMapList(json['buySellPoints'])
          .map(BuySellPoint.fromJson)
          .toList(),
      currentTrend: asText(json['currentTrend'], 'consolidation'),
      movementType: asText(json['movementType'], '盘整'),
      duration: asInt(json['duration']),
    );
  }
}

class PeriodData {
  const PeriodData({required this.result});

  final ChanlunResult result;

  factory PeriodData.fromJson(JsonMap json) {
    return PeriodData(
        result: ChanlunResult.fromJson(asJsonMap(json['result'])));
  }
}

class PeriodSummary {
  const PeriodSummary({
    required this.direction,
    required this.movementType,
    required this.hubCount,
    required this.biCount,
    required this.signalCount,
  });

  final String direction;
  final String movementType;
  final int hubCount;
  final int biCount;
  final int signalCount;

  factory PeriodSummary.fromJson(JsonMap json) {
    return PeriodSummary(
      direction: asText(json['direction'], 'consolidation'),
      movementType: asText(json['movementType'], '盘整'),
      hubCount: asInt(json['hubCount']),
      biCount: asInt(json['biCount']),
      signalCount: asInt(json['signalCount']),
    );
  }
}

class SignalItem {
  const SignalItem({
    required this.id,
    required this.type,
    required this.direction,
    required this.displayName,
    required this.price,
    required this.entryPrice,
    required this.stopLoss,
    required this.takeProfit,
    required this.date,
    required this.confirmed,
    required this.confidence,
    required this.compositeScore,
    required this.v14AlignScore,
    required this.v14AlignReasons,
  });

  final String id;
  final String type;
  final String direction;
  final String displayName;
  final double price;
  final double entryPrice;
  final double stopLoss;
  final double takeProfit;
  final String date;
  final bool confirmed;
  final String confidence;
  final int compositeScore;
  final int v14AlignScore;
  final List<String> v14AlignReasons;

  String get label => buySellLabels[type] ?? type;
  bool get isLong => direction == 'long';
  double get displayPrice => entryPrice == 0 ? price : entryPrice;

  factory SignalItem.fromJson(JsonMap json) {
    final reasons = json['v14AlignReasons'];
    return SignalItem(
      id: asText(json['id']),
      type: asText(json['type']),
      direction: asText(json['direction']),
      displayName: asText(json['displayName']),
      price: asDouble(json['price']),
      entryPrice: asDouble(json['entryPrice']),
      stopLoss: asDouble(json['stopLoss']),
      takeProfit: asDouble(json['takeProfit']),
      date: asText(json['date']),
      confirmed: asBool(json['confirmed']),
      confidence: asText(json['confidence']),
      compositeScore: asInt(json['compositeScore']),
      v14AlignScore: asInt(json['v14AlignScore']),
      v14AlignReasons: reasons is List
          ? reasons.map((item) => item.toString()).toList()
          : const [],
    );
  }
}

class ScanItem {
  const ScanItem({
    required this.market,
    required this.prefix,
    required this.exchange,
    required this.displayName,
    required this.trendCode,
    required this.executionCode,
    required this.lastPrice,
    required this.multiPeriod,
    required this.timeframeBars,
    required this.trend,
    required this.structure,
    required this.entry,
    required this.signals,
    required this.scannedAt,
  });

  final MarketType market;
  final String prefix;
  final String exchange;
  final String displayName;
  final String trendCode;
  final String executionCode;
  final double lastPrice;
  final Map<String, PeriodData> multiPeriod;
  final Map<String, List<Kline>> timeframeBars;
  final PeriodSummary trend;
  final PeriodSummary? structure;
  final PeriodSummary? entry;
  final List<SignalItem> signals;
  final String scannedAt;

  ChanlunResult? resultFor(String timeframe) => multiPeriod[timeframe]?.result;

  List<Kline> barsFor(String timeframe) {
    return timeframeBars[timeframe] ??
        resultFor(timeframe)?.mergedKlines ??
        const [];
  }

  factory ScanItem.fromJson(JsonMap json) {
    final periodJson = asJsonMap(json['multiPeriod']);
    final periods = <String, PeriodData>{};
    for (final entry in periodJson.entries) {
      periods[entry.key] = PeriodData.fromJson(asJsonMap(entry.value));
    }

    final barsJson = asJsonMap(json['timeframeBars']);
    final bars = <String, List<Kline>>{};
    for (final entry in barsJson.entries) {
      bars[entry.key] = asJsonMapList(entry.value).map(Kline.fromJson).toList();
    }

    return ScanItem(
      market: MarketType.parse(asText(json['market'], 'futures')),
      prefix: asText(json['prefix']),
      exchange: asText(json['exchange']),
      displayName: asText(json['displayName'], asText(json['prefix'])),
      trendCode: asText(json['trendCode']),
      executionCode: asText(json['executionCode']),
      lastPrice: asDouble(json['lastPrice']),
      multiPeriod: periods,
      timeframeBars: bars,
      trend: PeriodSummary.fromJson(asJsonMap(json['trend'])),
      structure: json['structure'] == null
          ? null
          : PeriodSummary.fromJson(asJsonMap(json['structure'])),
      entry: json['entry'] == null
          ? null
          : PeriodSummary.fromJson(asJsonMap(json['entry'])),
      signals: asJsonMapList(json['signals']).map(SignalItem.fromJson).toList(),
      scannedAt: asText(json['scannedAt']),
    );
  }
}

class ScanPayload {
  const ScanPayload({
    required this.results,
    required this.signals,
    required this.scannedAt,
    required this.message,
  });

  final List<ScanItem> results;
  final List<SignalItem> signals;
  final String scannedAt;
  final String message;

  bool get hasData => results.isNotEmpty;

  factory ScanPayload.fromJson(JsonMap json) {
    return ScanPayload(
      results: asJsonMapList(json['results']).map(ScanItem.fromJson).toList(),
      signals: asJsonMapList(json['signals']).map(SignalItem.fromJson).toList(),
      scannedAt: asText(json['scannedAt']),
      message: asText(json['message']),
    );
  }
}
