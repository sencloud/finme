import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/market_models.dart';

/// "管理品种" 底部弹窗 — 列表 + 添加 + 删除。
///
/// 输入: 当前 watchlist。
/// 关闭时: 通过 onSubmit 回调把"修改后的列表"返出去给上层(由上层负责持久化)。
///
/// 期货输入示例:  prefix=M  exchange=DCE   name=豆粕
/// A 股输入示例:  prefix=600519 exchange=SH name=贵州茅台
class WatchlistManagerSheet extends StatefulWidget {
  const WatchlistManagerSheet({
    super.key,
    required this.initial,
    required this.onSubmit,
  });

  final List<WatchItem> initial;
  final ValueChanged<List<WatchItem>> onSubmit;

  @override
  State<WatchlistManagerSheet> createState() => _WatchlistManagerSheetState();
}

class _WatchlistManagerSheetState extends State<WatchlistManagerSheet> {
  late List<WatchItem> _items;

  @override
  void initState() {
    super.initState();
    _items = List<WatchItem>.from(widget.initial);
  }

  void _remove(int index) {
    setState(() => _items.removeAt(index));
  }

  Future<void> _openAddDialog() async {
    final added = await showDialog<WatchItem>(
      context: context,
      builder: (_) => const _AddWatchItemDialog(),
    );
    if (added == null) return;

    // 同 market+prefix+exchange 视为重复,直接覆盖名字而不是新增,避免脏数据。
    setState(() {
      final existingIndex = _items.indexWhere((w) => w.key == added.key);
      if (existingIndex >= 0) {
        _items[existingIndex] = added;
      } else {
        _items.add(added);
      }
    });
  }

  void _save() {
    widget.onSubmit(_items);
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final viewInsets = MediaQuery.viewInsetsOf(context).bottom;
    return Padding(
      padding: EdgeInsets.fromLTRB(16, 8, 16, viewInsets + 16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Text('管理品种', style: Theme.of(context).textTheme.titleMedium),
              const Spacer(),
              IconButton(
                tooltip: '添加',
                onPressed: _openAddDialog,
                icon: const Icon(Icons.add_circle_outline),
              ),
            ],
          ),
          const SizedBox(height: 4),
          if (_items.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 32),
              child: Center(
                child: Text('还没有品种,点右上角 + 添加',
                    style: TextStyle(color: Colors.white60)),
              ),
            )
          else
            // 限高,避免品种很多时把整个屏幕顶出去。
            ConstrainedBox(
              constraints: BoxConstraints(
                maxHeight: MediaQuery.sizeOf(context).height * 0.45,
              ),
              child: ListView.separated(
                shrinkWrap: true,
                itemCount: _items.length,
                separatorBuilder: (_, __) => const Divider(height: 1),
                itemBuilder: (context, index) {
                  final item = _items[index];
                  return ListTile(
                    dense: true,
                    leading: _MarketBadge(market: item.market),
                    title: Text(item.name.isEmpty ? item.prefix : item.name),
                    subtitle: Text(item.displayCode,
                        style: const TextStyle(color: Colors.white54)),
                    trailing: IconButton(
                      tooltip: '删除',
                      onPressed: () => _remove(index),
                      icon: const Icon(Icons.delete_outline,
                          color: Colors.redAccent),
                    ),
                  );
                },
              ),
            ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('取消'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed: _save,
                  child: const Text('保存'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 添加单个品种的弹窗 — 选 market、填代码、填名称。
class _AddWatchItemDialog extends StatefulWidget {
  const _AddWatchItemDialog();

  @override
  State<_AddWatchItemDialog> createState() => _AddWatchItemDialogState();
}

class _AddWatchItemDialogState extends State<_AddWatchItemDialog> {
  MarketType _market = MarketType.futures;
  final _prefixController = TextEditingController();
  final _exchangeController = TextEditingController();
  final _nameController = TextEditingController();
  String? _validationError;

  static const _futuresExchanges = ['DCE', 'CZCE', 'SHFE', 'INE', 'CFFEX'];
  static const _stockExchanges = ['SH', 'SZ', 'BJ'];

  @override
  void dispose() {
    _prefixController.dispose();
    _exchangeController.dispose();
    _nameController.dispose();
    super.dispose();
  }

  void _switchMarket(MarketType m) {
    setState(() {
      _market = m;
      // 切换市场时清空交易所,避免 DCE 出现在股票里这种穿帮。
      _exchangeController.clear();
      _validationError = null;
    });
  }

  void _submit() {
    final prefix = _prefixController.text.trim().toUpperCase();
    final exchange = _exchangeController.text.trim().toUpperCase();
    final name = _nameController.text.trim();

    if (prefix.isEmpty || exchange.isEmpty) {
      setState(() => _validationError = '代码和交易所都要填');
      return;
    }

    final allowed =
        _market == MarketType.futures ? _futuresExchanges : _stockExchanges;
    if (!allowed.contains(exchange)) {
      setState(() => _validationError = '$_marketName交易所只能是: ${allowed.join(", ")}');
      return;
    }

    Navigator.of(context).pop(WatchItem(
      market: _market,
      prefix: prefix,
      exchange: exchange,
      name: name.isEmpty ? prefix : name,
    ));
  }

  String get _marketName => _market.label;

  @override
  Widget build(BuildContext context) {
    final isStock = _market == MarketType.stock;
    final allowedExchanges =
        isStock ? _stockExchanges : _futuresExchanges;

    return AlertDialog(
      title: const Text('添加品种'),
      content: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            SegmentedButton<MarketType>(
              segments: const [
                ButtonSegment(
                    value: MarketType.futures, label: Text('期货')),
                ButtonSegment(
                    value: MarketType.stock, label: Text('A股')),
              ],
              selected: {_market},
              onSelectionChanged: (set) => _switchMarket(set.first),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _prefixController,
              autofocus: true,
              textCapitalization: TextCapitalization.characters,
              inputFormatters: [
                // 期货 prefix 是字母(M / SR),股票 prefix 是 6 位数字(600519)。
                // 二者都用 ASCII 字母数字范围来限制即可,提交时再上层转大写。
                FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9]')),
              ],
              decoration: InputDecoration(
                labelText: isStock ? '股票代码 (如 600519)' : '期货品种代码 (如 M)',
                isDense: true,
              ),
            ),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              // ignore: deprecated_member_use
              value: _exchangeController.text.isEmpty
                  ? null
                  : _exchangeController.text,
              decoration: const InputDecoration(
                labelText: '交易所',
                isDense: true,
              ),
              items: allowedExchanges
                  .map((e) => DropdownMenuItem(value: e, child: Text(e)))
                  .toList(),
              onChanged: (v) => setState(() {
                _exchangeController.text = v ?? '';
                _validationError = null;
              }),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _nameController,
              decoration: InputDecoration(
                labelText: isStock ? '股票名称 (如 贵州茅台)' : '品种中文名 (如 豆粕)',
                isDense: true,
              ),
            ),
            if (_validationError != null) ...[
              const SizedBox(height: 8),
              Text(_validationError!,
                  style: const TextStyle(color: Colors.redAccent)),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submit,
          child: const Text('添加'),
        ),
      ],
    );
  }
}

class _MarketBadge extends StatelessWidget {
  const _MarketBadge({required this.market});

  final MarketType market;

  @override
  Widget build(BuildContext context) {
    final isStock = market == MarketType.stock;
    final color = isStock ? const Color(0xFFEF4444) : const Color(0xFF3B82F6);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        // ignore: deprecated_member_use
        color: color.withOpacity(0.15),
        // ignore: deprecated_member_use
        border: Border.all(color: color.withOpacity(0.6)),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        market.label,
        style: TextStyle(
            color: color, fontSize: 11, fontWeight: FontWeight.w700),
      ),
    );
  }
}
