import 'package:flutter/material.dart';

import '../models/market_models.dart';

class PeriodPanel extends StatelessWidget {
  const PeriodPanel({
    required this.item,
    required this.selectedTimeframe,
    required this.onTimeframeChanged,
    this.compact = false,
    super.key,
  });

  final ScanItem item;
  final String selectedTimeframe;
  final ValueChanged<String> onTimeframeChanged;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final timeframes = _availableTimeframes(item);

    if (compact) {
      return Container(
        padding: const EdgeInsets.fromLTRB(10, 6, 10, 7),
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
            Row(
              children: [
                SizedBox(
                  width: 86,
                  child: Text(
                    item.lastPrice == 0
                        ? '--'
                        : item.lastPrice.toStringAsFixed(2),
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w900,
                      height: 1,
                    ),
                  ),
                ),
                Expanded(
                  child: SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        for (final tf in timeframes) ...[
                          ChoiceChip(
                            label: Text(_timeframeName(tf)),
                            selected: tf == selectedTimeframe,
                            onSelected: (_) => onTimeframeChanged(tf),
                            showCheckmark: false,
                            visualDensity: const VisualDensity(
                                horizontal: -4, vertical: -4),
                            materialTapTargetSize:
                                MaterialTapTargetSize.shrinkWrap,
                            labelStyle: const TextStyle(
                                fontSize: 12, fontWeight: FontWeight.w700),
                            labelPadding:
                                const EdgeInsets.symmetric(horizontal: 4),
                            padding: const EdgeInsets.symmetric(horizontal: 4),
                          ),
                          const SizedBox(width: 6),
                        ],
                      ],
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 5),
            _CompactSummaryRow(
                item: item, selectedTimeframe: selectedTimeframe),
          ],
        ),
      );
    }

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    '${item.displayName} 多周期结构',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Text(
                  item.executionCode.isEmpty
                      ? item.trendCode
                      : item.executionCode,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: Colors.white60),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final tf in timeframes)
                  ChoiceChip(
                    label: Text(_timeframeName(tf)),
                    selected: tf == selectedTimeframe,
                    onSelected: (_) => onTimeframeChanged(tf),
                  ),
              ],
            ),
            const SizedBox(height: 14),
            LayoutBuilder(
              builder: (context, constraints) {
                final wide = constraints.maxWidth > 720;
                final cards = [
                  _SummaryCard(title: '日线', summary: item.trend),
                  _SummaryCard(title: '1小时', summary: item.structure),
                  _SummaryCard(title: '15分钟', summary: item.entry),
                  _ActivePeriodCard(
                    title: _timeframeName(selectedTimeframe),
                    result: item.resultFor(selectedTimeframe),
                  ),
                ];

                return GridView.count(
                  crossAxisCount: wide ? 4 : 2,
                  childAspectRatio: wide ? 1.75 : 1.55,
                  crossAxisSpacing: 10,
                  mainAxisSpacing: 10,
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  children: cards,
                );
              },
            ),
          ],
        ),
      ),
    );
  }

  static List<String> _availableTimeframes(ScanItem item) {
    const order = ['1w', '1d', '1h', '15m'];
    final result = order.where(item.multiPeriod.containsKey).toList();
    return result.isEmpty ? ['15m'] : result;
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

class _CompactSummaryRow extends StatelessWidget {
  const _CompactSummaryRow({
    required this.item,
    required this.selectedTimeframe,
  });

  final ScanItem item;
  final String selectedTimeframe;

  @override
  Widget build(BuildContext context) {
    final result = item.resultFor(selectedTimeframe);
    final latest = result?.buySellPoints.isEmpty == false
        ? result!.buySellPoints.last
        : null;
    final trendColor = _trendColor(result?.currentTrend ?? '');
    final signalColor = latest == null
        ? Colors.white54
        : (latest.isBuy ? const Color(0xFFEF4444) : const Color(0xFF10B981));

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DefaultTextStyle(
        style: const TextStyle(
            fontSize: 11, color: Colors.white54, fontWeight: FontWeight.w700),
        child: Row(
          children: [
            Text(result?.movementType ?? '暂无',
                style: TextStyle(color: trendColor)),
            const SizedBox(width: 10),
            Text('中枢 ${result?.hubs.length ?? 0}'),
            const SizedBox(width: 10),
            Text('笔 ${result?.bis.length ?? 0}'),
            const SizedBox(width: 10),
            Text('买卖点 ${latest?.label ?? '无'}',
                style: TextStyle(color: signalColor)),
          ],
        ),
      ),
    );
  }
}

class _SummaryCard extends StatelessWidget {
  const _SummaryCard({required this.title, required this.summary});

  final String title;
  final PeriodSummary? summary;

  @override
  Widget build(BuildContext context) {
    if (summary == null) {
      return const _MetricShell(title: '无数据', child: Text('该周期暂无数据'));
    }

    final data = summary!;
    return _MetricShell(
      title: title,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            data.movementType,
            style: TextStyle(
              color: _trendColor(data.direction),
              fontSize: 17,
              fontWeight: FontWeight.w700,
            ),
          ),
          const Spacer(),
          Text('中枢 ${data.hubCount}  笔 ${data.biCount}'),
          Text('信号 ${data.signalCount}',
              style: const TextStyle(color: Colors.white60)),
        ],
      ),
    );
  }
}

class _ActivePeriodCard extends StatelessWidget {
  const _ActivePeriodCard({required this.title, required this.result});

  final String title;
  final ChanlunResult? result;

  @override
  Widget build(BuildContext context) {
    if (result == null) {
      return _MetricShell(title: title, child: const Text('暂无结构'));
    }

    final latest =
        result!.buySellPoints.isEmpty ? null : result!.buySellPoints.last;
    return _MetricShell(
      title: title,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            result!.movementType,
            style: TextStyle(
              color: _trendColor(result!.currentTrend),
              fontSize: 17,
              fontWeight: FontWeight.w700,
            ),
          ),
          const Spacer(),
          Text('中枢 ${result!.hubs.length}  笔 ${result!.bis.length}'),
          Text(
            latest == null
                ? '暂无买卖点'
                : '${latest.label} @ ${latest.price.toStringAsFixed(2)}',
            style: const TextStyle(color: Colors.white60),
          ),
        ],
      ),
    );
  }
}

class _MetricShell extends StatelessWidget {
  const _MetricShell({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFF1A2332),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFF2A3A50)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title,
              style: Theme.of(context)
                  .textTheme
                  .labelMedium
                  ?.copyWith(color: Colors.white60)),
          const SizedBox(height: 8),
          Expanded(child: child),
        ],
      ),
    );
  }
}

Color _trendColor(String direction) {
  return switch (direction) {
    'up' => const Color(0xFFEF4444),
    'down' => const Color(0xFF10B981),
    _ => const Color(0xFFF59E0B),
  };
}
