import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models/market_models.dart';

class KlineChart extends StatefulWidget {
  const KlineChart({
    required this.item,
    required this.timeframe,
    this.mobile = false,
    super.key,
  });

  final ScanItem item;
  final String timeframe;
  final bool mobile;

  @override
  State<KlineChart> createState() => _KlineChartState();
}

class _KlineChartState extends State<KlineChart> {
  double _visibleBars = 86;
  double _endIndex = 0;
  double _scaleStartVisible = 86;

  List<Kline> get _bars => widget.item.barsFor(widget.timeframe);

  @override
  void initState() {
    super.initState();
    _resetViewport();
  }

  @override
  void didUpdateWidget(covariant KlineChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.item.prefix != widget.item.prefix ||
        oldWidget.timeframe != widget.timeframe ||
        oldWidget.item.barsFor(oldWidget.timeframe).length != _bars.length) {
      _resetViewport();
    } else {
      _clampViewport();
    }
  }

  void _resetViewport() {
    final count = _bars.length;
    _visibleBars =
        math.min(widget.mobile ? 86 : 140, math.max(count, 1)).toDouble();
    _endIndex = count.toDouble();
  }

  void _clampViewport() {
    final count = _bars.length;
    if (count == 0) {
      _visibleBars = widget.mobile ? 86 : 140;
      _endIndex = 0;
      return;
    }
    _visibleBars = _visibleBars.clamp(24.0, count.toDouble());
    _endIndex = _endIndex.clamp(_visibleBars, count.toDouble());
  }

  @override
  Widget build(BuildContext context) {
    final bars = _bars;
    final result = widget.item.resultFor(widget.timeframe);

    return Container(
      decoration: const BoxDecoration(
        color: Color(0xFF0B1220),
        border: Border(
          top: BorderSide(color: Color(0xFF1F2937)),
          bottom: BorderSide(color: Color(0xFF1F2937)),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ChartToolbar(
            title:
                '${widget.item.displayName} · ${_timeframeName(widget.timeframe)}',
            subtitle: bars.isEmpty ? '无K线' : '${bars.length} 根 · 双指缩放，拖动查看历史',
            onReset: _resetAndRefresh,
          ),
          LayoutBuilder(
            builder: (context, constraints) {
              final chartHeight = widget.mobile
                  ? (MediaQuery.sizeOf(context).height * 0.64)
                      .clamp(460.0, 660.0)
                  : 460.0;
              return SizedBox(
                height: chartHeight,
                child: bars.isEmpty || result == null
                    ? const Center(
                        child: Text('暂无该周期数据',
                            style: TextStyle(color: Colors.white60)),
                      )
                    : GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onScaleStart: (_) => _scaleStartVisible = _visibleBars,
                        onScaleUpdate: (details) {
                          final chartWidth = math.max(
                              constraints.maxWidth -
                                  _KlinePainter.rightPad -
                                  _KlinePainter.leftPad,
                              1);
                          final slotWidth =
                              chartWidth / math.max(_visibleBars, 1);
                          setState(() {
                            if ((details.scale - 1).abs() > 0.01) {
                              _visibleBars =
                                  (_scaleStartVisible / details.scale)
                                      .clamp(24.0, bars.length.toDouble());
                            }
                            _endIndex = (_endIndex -
                                    details.focalPointDelta.dx / slotWidth)
                                .clamp(_visibleBars, bars.length.toDouble());
                          });
                        },
                        onDoubleTap: _resetAndRefresh,
                        child: CustomPaint(
                          painter: _KlinePainter(
                            bars: bars,
                            bis: result.bis,
                            hubs: result.hubs,
                            points: result.buySellPoints,
                            visibleBars:
                                _visibleBars.round().clamp(1, bars.length),
                            endIndex: _endIndex.round().clamp(1, bars.length),
                          ),
                          child: const SizedBox.expand(),
                        ),
                      ),
              );
            },
          ),
        ],
      ),
    );
  }

  void _resetAndRefresh() {
    setState(_resetViewport);
  }

  static String _timeframeName(String tf) {
    return switch (tf) {
      '1w' => '周线',
      '1d' => '日线',
      '1h' => '1小时',
      '15m' => '15分钟',
      _ => tf,
    };
  }
}

class _ChartToolbar extends StatelessWidget {
  const _ChartToolbar({
    required this.title,
    required this.subtitle,
    required this.onReset,
  });

  final String title;
  final String subtitle;
  final VoidCallback onReset;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 6, 6, 4),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title,
                    style: const TextStyle(
                        fontWeight: FontWeight.w800, fontSize: 15)),
                const SizedBox(height: 2),
                Text(subtitle,
                    style:
                        const TextStyle(color: Colors.white38, fontSize: 11)),
              ],
            ),
          ),
          IconButton(
            tooltip: '回到最新',
            onPressed: onReset,
            icon: const Icon(Icons.keyboard_double_arrow_right, size: 20),
          ),
        ],
      ),
    );
  }
}

class _BollPoint {
  const _BollPoint({
    required this.upper,
    required this.middle,
    required this.lower,
  });

  final double upper;
  final double middle;
  final double lower;
}

class _KlinePainter extends CustomPainter {
  _KlinePainter({
    required this.bars,
    required this.bis,
    required this.hubs,
    required this.points,
    required this.visibleBars,
    required this.endIndex,
  });

  final List<Kline> bars;
  final List<Bi> bis;
  final List<Hub> hubs;
  final List<BuySellPoint> points;
  final int visibleBars;
  final int endIndex;

  static const leftPad = 8.0;
  static const rightPad = 58.0;
  static const _topPad = 14.0;
  static const _bottomPad = 30.0;

  @override
  void paint(Canvas canvas, Size size) {
    if (bars.isEmpty || size.width <= 0 || size.height <= 0) {
      return;
    }

    final end = endIndex.clamp(1, bars.length);
    final start = math.max(0, end - visibleBars);
    final visible = bars.sublist(start, end);
    if (visible.isEmpty) {
      return;
    }

    final chartWidth = size.width - leftPad - rightPad;
    final chartHeight = size.height - _topPad - _bottomPad;
    final slotWidth = chartWidth / visible.length;
    final candleWidth = math.max(1.8, math.min(10.0, slotWidth * 0.58));

    final boll = _calculateBoll();
    var minPrice = visible.map((bar) => bar.low).reduce(math.min);
    var maxPrice = visible.map((bar) => bar.high).reduce(math.max);
    for (var i = start; i < end; i++) {
      final item = boll[i];
      if (item == null) {
        continue;
      }
      minPrice = math.min(minPrice, item.lower);
      maxPrice = math.max(maxPrice, item.upper);
    }
    for (final hub in hubs) {
      if (hub.endIndex >= start && hub.startIndex < end) {
        minPrice = math.min(minPrice, hub.zd);
        maxPrice = math.max(maxPrice, hub.zg);
      }
    }
    final padding = math.max((maxPrice - minPrice) * 0.08, 1.0);
    minPrice -= padding;
    maxPrice += padding;

    double xForIndex(int index) {
      final clamped = index.clamp(start, end - 1);
      return leftPad + (clamped - start + 0.5) * slotWidth;
    }

    double yForPrice(double price) {
      final ratio = (maxPrice - price) / (maxPrice - minPrice);
      return _topPad + ratio * chartHeight;
    }

    _drawGrid(canvas, size, minPrice, maxPrice, yForPrice);
    _drawHubs(canvas, start, end, xForIndex, yForPrice);
    _drawCandles(canvas, visible, start, candleWidth, xForIndex, yForPrice);
    _drawBoll(canvas, size, boll, start, end, xForIndex, yForPrice);
    _drawBis(canvas, start, end, xForIndex, yForPrice);
    _drawBuySellPoints(canvas, size, start, end, xForIndex, yForPrice);
    _drawDateAxis(canvas, size, visible, start, xForIndex);
  }

  List<_BollPoint?> _calculateBoll({int period = 20, double multiple = 2}) {
    final result = List<_BollPoint?>.filled(bars.length, null);
    if (bars.length < period) {
      return result;
    }

    var sum = 0.0;
    var sumSquares = 0.0;
    for (var i = 0; i < bars.length; i++) {
      final close = bars[i].close;
      sum += close;
      sumSquares += close * close;

      if (i >= period) {
        final old = bars[i - period].close;
        sum -= old;
        sumSquares -= old * old;
      }

      if (i >= period - 1) {
        final middle = sum / period;
        final variance = math.max(sumSquares / period - middle * middle, 0);
        final stdDev = math.sqrt(variance);
        result[i] = _BollPoint(
          upper: middle + multiple * stdDev,
          middle: middle,
          lower: middle - multiple * stdDev,
        );
      }
    }
    return result;
  }

  void _drawGrid(
    Canvas canvas,
    Size size,
    double minPrice,
    double maxPrice,
    double Function(double price) yForPrice,
  ) {
    final gridPaint = Paint()
      ..color = const Color(0xFF1F2937)
      ..strokeWidth = 0.6;
    final strongGridPaint = Paint()
      ..color = const Color(0xFF273244)
      ..strokeWidth = 0.8;
    final textStyle = const TextStyle(color: Color(0xFF94A3B8), fontSize: 10);

    for (var i = 0; i <= 4; i++) {
      final price = minPrice + (maxPrice - minPrice) * i / 4;
      final y = yForPrice(price);
      canvas.drawLine(Offset(leftPad, y), Offset(size.width - rightPad, y),
          i == 2 ? strongGridPaint : gridPaint);
      _drawText(canvas, price.toStringAsFixed(2),
          Offset(size.width - rightPad + 6, y - 7), textStyle);
    }
  }

  void _drawHubs(
    Canvas canvas,
    int start,
    int end,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
  ) {
    final fillPaint = Paint()..color = const Color(0x243B82F6);
    final borderPaint = Paint()
      ..color = const Color(0xAA2563EB)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;

    for (final hub in hubs) {
      if (hub.endIndex < start || hub.startIndex >= end) {
        continue;
      }
      final left = xForIndex(hub.startIndex) - 4;
      final right = xForIndex(hub.endIndex) + 4;
      final top = yForPrice(hub.zg);
      final bottom = yForPrice(hub.zd);
      final rect = Rect.fromLTRB(left, top, right, bottom);
      canvas.drawRect(rect, fillPaint);
      canvas.drawRect(rect, borderPaint);
      _drawText(
        canvas,
        '中枢 L${hub.level}',
        Offset(left + 4, top + 4),
        const TextStyle(
            color: Color(0xFF60A5FA),
            fontSize: 10,
            fontWeight: FontWeight.w700),
      );
    }
  }

  void _drawBoll(
    Canvas canvas,
    Size size,
    List<_BollPoint?> boll,
    int start,
    int end,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
  ) {
    final upperPaint = Paint()
      ..color = const Color(0xFFE879F9)
      ..strokeWidth = 1.35
      ..style = PaintingStyle.stroke;
    final middlePaint = Paint()
      ..color = const Color(0xFF60A5FA)
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;
    final lowerPaint = Paint()
      ..color = const Color(0xFF94A3B8)
      ..strokeWidth = 1
      ..style = PaintingStyle.stroke;

    _drawBollLine(canvas, boll, start, end, xForIndex, yForPrice,
        (p) => p.upper, upperPaint);
    _drawBollLine(canvas, boll, start, end, xForIndex, yForPrice,
        (p) => p.middle, middlePaint);
    _drawBollLine(canvas, boll, start, end, xForIndex, yForPrice,
        (p) => p.lower, lowerPaint);

    final latest = _latestVisibleBoll(boll, start, end);
    final legend = latest == null
        ? 'BOLL(20,2)'
        : 'BOLL(20,2)  上 ${latest.upper.toStringAsFixed(2)}  中 ${latest.middle.toStringAsFixed(2)}  下 ${latest.lower.toStringAsFixed(2)}';
    _drawText(
      canvas,
      legend,
      const Offset(leftPad + 4, _topPad + 4),
      const TextStyle(
          color: Color(0xFFE879F9), fontSize: 10, fontWeight: FontWeight.w700),
    );
  }

  void _drawBollLine(
    Canvas canvas,
    List<_BollPoint?> boll,
    int start,
    int end,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
    double Function(_BollPoint point) selector,
    Paint paint,
  ) {
    final path = Path();
    var started = false;
    for (var i = start; i < end; i++) {
      final point = boll[i];
      if (point == null) {
        started = false;
        continue;
      }
      final x = xForIndex(i);
      final y = yForPrice(selector(point));
      if (!started) {
        path.moveTo(x, y);
        started = true;
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(path, paint);
  }

  _BollPoint? _latestVisibleBoll(List<_BollPoint?> boll, int start, int end) {
    for (var i = end - 1; i >= start; i--) {
      final item = boll[i];
      if (item != null) {
        return item;
      }
    }
    return null;
  }

  void _drawCandles(
    Canvas canvas,
    List<Kline> visible,
    int start,
    double candleWidth,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
  ) {
    final wickPaint = Paint()..strokeWidth = 1;
    final bodyPaint = Paint()..style = PaintingStyle.fill;

    for (var i = 0; i < visible.length; i++) {
      final bar = visible[i];
      final x = xForIndex(start + i);
      final color =
          bar.isUp ? const Color(0xFFEF4444) : const Color(0xFF22C55E);
      wickPaint.color = color;
      bodyPaint.color = color;

      final highY = yForPrice(bar.high);
      final lowY = yForPrice(bar.low);
      final openY = yForPrice(bar.open);
      final closeY = yForPrice(bar.close);
      canvas.drawLine(Offset(x, highY), Offset(x, lowY), wickPaint);

      final top = math.min(openY, closeY);
      final bottom = math.max(openY, closeY);
      canvas.drawRect(
        Rect.fromLTRB(x - candleWidth / 2, top, x + candleWidth / 2,
            math.max(bottom, top + 1)),
        bodyPaint,
      );
    }
  }

  void _drawBis(
    Canvas canvas,
    int start,
    int end,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
  ) {
    final paint = Paint()
      ..color = const Color(0xFFEAB308)
      ..strokeWidth = 1.4
      ..style = PaintingStyle.stroke;
    final unfinishedPaint = Paint()
      ..color = const Color(0xFFF59E0B)
      ..strokeWidth = 1.4
      ..style = PaintingStyle.stroke;

    for (final bi in bis) {
      final startIndex = bi.startFractal.klineIndex;
      final endIndex = bi.endFractal.klineIndex;
      if (endIndex < start || startIndex >= end) {
        continue;
      }

      final startPrice = bi.startFractal.type == 'top'
          ? bi.startFractal.high
          : bi.startFractal.low;
      final endPrice =
          bi.endFractal.type == 'top' ? bi.endFractal.high : bi.endFractal.low;
      canvas.drawLine(
        Offset(xForIndex(startIndex), yForPrice(startPrice)),
        Offset(xForIndex(endIndex), yForPrice(endPrice)),
        bi.finished ? paint : unfinishedPaint,
      );
    }
  }

  void _drawBuySellPoints(
    Canvas canvas,
    Size size,
    int start,
    int end,
    double Function(int index) xForIndex,
    double Function(double price) yForPrice,
  ) {
    final occupied = <Rect>[];
    final visiblePoints = points
        .where((point) => point.index >= start && point.index < end)
        .toList()
      ..sort((a, b) => a.index.compareTo(b.index));

    for (final point in visiblePoints) {
      final x = xForIndex(point.index);
      final y = yForPrice(point.price);
      final color =
          point.isBuy ? const Color(0xFFEF4444) : const Color(0xFF22C55E);
      final markerPaint = Paint()
        ..color = point.biFinished ? color : const Color(0xFFF59E0B);
      _drawMarker(canvas, point, x, y, markerPaint);
      _drawSignalLabel(canvas, size, point, x, y, markerPaint.color, occupied);
    }
  }

  void _drawMarker(
      Canvas canvas, BuySellPoint point, double x, double y, Paint paint) {
    final path = Path();
    if (point.isBuy) {
      path
        ..moveTo(x, y - 9)
        ..lineTo(x - 5, y + 3)
        ..lineTo(x + 5, y + 3)
        ..close();
    } else {
      path
        ..moveTo(x, y + 9)
        ..lineTo(x - 5, y - 3)
        ..lineTo(x + 5, y - 3)
        ..close();
    }
    canvas.drawPath(path, paint);
  }

  void _drawSignalLabel(
    Canvas canvas,
    Size size,
    BuySellPoint point,
    double x,
    double y,
    Color color,
    List<Rect> occupied,
  ) {
    final painter = TextPainter(
      text: TextSpan(
        text: point.label,
        style: const TextStyle(
            color: Colors.white, fontSize: 10, fontWeight: FontWeight.w800),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
    )..layout();

    final width = painter.width + 10;
    final height = painter.height + 5;
    Rect? chosen;

    for (var level = 0; level < 8; level++) {
      final xPos =
          (x - width / 2).clamp(leftPad, size.width - rightPad - width);
      final yBase = point.isBuy ? y - 30 - level * 16 : y + 14 + level * 16;
      final yPos = yBase.clamp(_topPad, size.height - _bottomPad - height);
      final rect =
          Rect.fromLTWH(xPos.toDouble(), yPos.toDouble(), width, height);
      final overlaps = occupied.any((item) => item.inflate(2).overlaps(rect));
      if (!overlaps) {
        chosen = rect;
        break;
      }
    }

    chosen ??= Rect.fromLTWH(
      (x - width / 2).clamp(leftPad, size.width - rightPad - width).toDouble(),
      (point.isBuy ? y - 30 : y + 14)
          .clamp(_topPad, size.height - _bottomPad - height)
          .toDouble(),
      width,
      height,
    );

    final bgPaint = Paint()..color = color.withAlpha(210);
    final borderPaint = Paint()
      ..color = Colors.white.withAlpha(80)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.8;
    final rrect = RRect.fromRectAndRadius(chosen, const Radius.circular(4));
    canvas.drawRRect(rrect, bgPaint);
    canvas.drawRRect(rrect, borderPaint);
    painter.paint(canvas, Offset(chosen.left + 5, chosen.top + 2));
    occupied.add(chosen);
  }

  void _drawDateAxis(
    Canvas canvas,
    Size size,
    List<Kline> visible,
    int start,
    double Function(int index) xForIndex,
  ) {
    if (visible.isEmpty) {
      return;
    }
    final textStyle = const TextStyle(color: Color(0xFF64748B), fontSize: 10);
    final step = math.max(1, visible.length ~/ 4);
    for (var i = 0; i < visible.length; i += step) {
      final date = visible[i].date;
      final label = date.length > 10
          ? date.substring(5, math.min(16, date.length))
          : date;
      _drawText(canvas, label,
          Offset(xForIndex(start + i) - 24, size.height - 18), textStyle);
    }
  }

  void _drawText(Canvas canvas, String text, Offset offset, TextStyle style) {
    final painter = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
      maxLines: 1,
    )..layout();
    painter.paint(canvas, offset);
  }

  @override
  bool shouldRepaint(covariant _KlinePainter oldDelegate) {
    return oldDelegate.bars != bars ||
        oldDelegate.bis != bis ||
        oldDelegate.hubs != hubs ||
        oldDelegate.points != points ||
        oldDelegate.visibleBars != visibleBars ||
        oldDelegate.endIndex != endIndex;
  }
}
