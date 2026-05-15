import 'package:flutter/material.dart';

import '../models/market_models.dart';

class SignalList extends StatelessWidget {
  const SignalList({
    required this.signals,
    this.title = '信号',
    super.key,
  });

  final List<SignalItem> signals;
  final String title;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                    child: Text(title,
                        style: Theme.of(context).textTheme.titleMedium)),
                Text('${signals.length} 条',
                    style: const TextStyle(color: Colors.white60)),
              ],
            ),
            const SizedBox(height: 12),
            if (signals.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Center(
                  child: Text('暂无信号', style: TextStyle(color: Colors.white60)),
                ),
              )
            else
              ...signals.map((signal) => _SignalTile(signal: signal)),
          ],
        ),
      ),
    );
  }
}

class _SignalTile extends StatelessWidget {
  const _SignalTile({required this.signal});

  final SignalItem signal;

  @override
  Widget build(BuildContext context) {
    final directionColor =
        signal.isLong ? const Color(0xFFEF4444) : const Color(0xFF10B981);
    final bgColor =
        signal.isLong ? const Color(0x1AEF4444) : const Color(0x1A10B981);

    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: directionColor.withAlpha(90)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: directionColor.withAlpha(32),
                  borderRadius: BorderRadius.circular(999),
                ),
                child: Text(
                  '${signal.label} ${signal.isLong ? '做多' : '做空'}',
                  style: TextStyle(
                      color: directionColor, fontWeight: FontWeight.w700),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  signal.displayName,
                  style: const TextStyle(fontWeight: FontWeight.w700),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              _ConfirmBadge(confirmed: signal.confirmed),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            children: [
              _PriceBox(label: '入场', value: signal.displayPrice),
              _PriceBox(label: '止损', value: signal.stopLoss),
              _PriceBox(label: '止盈', value: signal.takeProfit),
              _TextBox(
                  label: '评分',
                  value: '${signal.compositeScore}/${signal.v14AlignScore}'),
              if (signal.confidence.isNotEmpty)
                _TextBox(label: '信心', value: signal.confidence),
            ],
          ),
          if (signal.v14AlignReasons.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              signal.v14AlignReasons.join('，'),
              style: const TextStyle(color: Colors.white60, fontSize: 12),
            ),
          ],
        ],
      ),
    );
  }
}

class _ConfirmBadge extends StatelessWidget {
  const _ConfirmBadge({required this.confirmed});

  final bool confirmed;

  @override
  Widget build(BuildContext context) {
    return Text(
      confirmed ? '确认' : '待确认',
      style: TextStyle(
        color: confirmed ? const Color(0xFF10B981) : const Color(0xFFF59E0B),
        fontWeight: FontWeight.w700,
        fontSize: 12,
      ),
    );
  }
}

class _PriceBox extends StatelessWidget {
  const _PriceBox({required this.label, required this.value});

  final String label;
  final double value;

  @override
  Widget build(BuildContext context) {
    return _TextBox(
        label: label, value: value == 0 ? '-' : value.toStringAsFixed(2));
  }
}

class _TextBox extends StatelessWidget {
  const _TextBox({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 92,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0x80111827),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF2A3A50)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(color: Colors.white54, fontSize: 11)),
          const SizedBox(height: 2),
          Text(value, style: const TextStyle(fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }
}
