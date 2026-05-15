import 'dart:async';

import 'package:flutter/material.dart';

import '../api/finme_api.dart';
import '../config/app_config.dart';
import '../data/watchlist_repository.dart';
import '../models/market_models.dart';
import '../widgets/kline_chart.dart';
import '../widgets/period_panel.dart';
import '../widgets/signal_list.dart';
import '../widgets/watchlist_manager_sheet.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  late final TextEditingController _baseUrlController;
  late FinmeApi _api;
  final WatchlistRepository _watchlistRepo = WatchlistRepository();

  JsonMap? _status;
  // 真实显示用的"我的自选" — 取自本地存储 + 后端种子合并。
  List<WatchItem> _watchlist = const [];
  ScanPayload? _scan;
  String? _selectedKey; // = WatchItem.key, 不再只用 prefix(股票期货 prefix 可能撞)
  String _selectedTimeframe = '15m';
  String? _error;
  int _tabIndex = 0;
  bool _loading = true;
  bool _scanning = false; // 单独标记"扫描进行中",避免和 loading 抢

  @override
  void initState() {
    super.initState();
    _baseUrlController = TextEditingController(text: AppConfig.defaultBaseUrl);
    _api = FinmeApi(AppConfig.defaultBaseUrl);
    _loadInitial();
  }

  @override
  void dispose() {
    _baseUrlController.dispose();
    super.dispose();
  }

  /// 启动初始化:
  /// 1. 取后端 status
  /// 2. 加载本地 watchlist;若空则从后端 /api/watchlist 拉一份种子写入本地
  /// 3. 取上次扫描结果展示
  Future<void> _loadInitial() async {
    setState(() {
      _loading = true;
      _error = null;
    });

    try {
      final status = await _api.fetchStatus();

      // 先尝试本地 watchlist;空则用后端的种子。
      var localWatch = await _watchlistRepo.load();
      if (localWatch.isEmpty) {
        try {
          final seed = await _api.fetchWatchlist();
          if (seed.watchlist.isNotEmpty) {
            localWatch = seed.watchlist;
            await _watchlistRepo.save(localWatch);
          }
        } catch (_) {
          // 种子失败不致命 — 用户还可以手动添加。
        }
      }

      final lastScan = await _api.fetchLastScan();

      if (!mounted) return;
      setState(() {
        _status = status;
        _watchlist = localWatch;
        _scan = lastScan;
        _selectedKey = _pickSelectedKey(lastScan, localWatch);
      });
    } on TimeoutException {
      _setError('连接 ${_api.baseUrl} 超时');
    } catch (error) {
      _setError(error.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _connect() async {
    _api = FinmeApi(_baseUrlController.text);
    await _loadInitial();
  }

  /// 用户在管理弹窗里改完品种 -> 保存到本地 -> 立刻按新列表触发一次扫描。
  Future<void> _updateWatchlist(List<WatchItem> next) async {
    await _watchlistRepo.save(next);
    if (!mounted) return;
    setState(() => _watchlist = next);
    await _runScan();
  }

  /// 主动触发后端扫描。前端把当前自选列表发过去,后端按这个列表实时扫。
  Future<void> _runScan() async {
    if (_watchlist.isEmpty) {
      _setError('请先添加品种');
      return;
    }
    setState(() {
      _scanning = true;
      _error = null;
    });

    try {
      final result = await _api.triggerScan(watchlist: _watchlist);
      if (!mounted) return;
      setState(() {
        _scan = result;
        _selectedKey = _pickSelectedKey(result, _watchlist);
      });
    } on TimeoutException {
      _setError('扫描超时,稍后再试');
    } catch (error) {
      _setError('扫描失败: $error');
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  void _setError(String message) {
    if (!mounted) return;
    setState(() => _error = message);
  }

  /// 在扫描结果 + 自选列表中挑一个默认选中项的 key。
  /// 优先级: 之前选中的(若仍存在) > 第一个有结果的品种 > 自选第一个。
  String? _pickSelectedKey(ScanPayload scan, List<WatchItem> watchlist) {
    String keyOfScan(ScanItem s) => '${s.market.id}:${s.prefix}.${s.exchange}';

    if (_selectedKey != null &&
        scan.results.any((item) => keyOfScan(item) == _selectedKey)) {
      return _selectedKey;
    }
    if (scan.results.isNotEmpty) return keyOfScan(scan.results.first);
    if (watchlist.isNotEmpty) return watchlist.first.key;
    return null;
  }

  ScanItem? get _selectedItem {
    final scan = _scan;
    final selected = _selectedKey;
    if (scan == null || selected == null) return null;
    for (final item in scan.results) {
      final k = '${item.market.id}:${item.prefix}.${item.exchange}';
      if (k == selected) return item;
    }
    return scan.results.isEmpty ? null : scan.results.first;
  }

  @override
  Widget build(BuildContext context) {
    final selectedItem = _selectedItem;

    final busy = _loading || _scanning;

    return Scaffold(
      appBar: AppBar(
        title: Text(_tabIndex == 0 ? '看盘' : '信号'),
        actions: [
          IconButton(
            tooltip: '管理品种',
            onPressed: busy ? null : _showWatchlistManager,
            icon: const Icon(Icons.playlist_add_check),
          ),
          IconButton(
            tooltip: _scanning ? '扫描中' : '扫描',
            onPressed: busy ? null : _runScan,
            icon: _scanning
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(
                        strokeWidth: 2.2, color: Colors.white))
                : const Icon(Icons.radar),
          ),
          IconButton(
            tooltip: '刷新',
            onPressed: busy ? null : _loadInitial,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: '连接设置',
            onPressed: _showConnectionSheet,
            icon: const Icon(Icons.tune),
          ),
        ],
      ),
      body: IndexedStack(
        index: _tabIndex,
        children: [
          _MarketTab(
            loading: _loading,
            error: _error,
            status: _status,
            watchlist: _watchlist,
            scan: _scan,
            selectedItem: selectedItem,
            selectedKey: _selectedKey,
            selectedTimeframe: _selectedTimeframe,
            onRefresh: _loadInitial,
            onSelectedKey: (key) => setState(() => _selectedKey = key),
            onTimeframeChanged: (timeframe) =>
                setState(() => _selectedTimeframe = timeframe),
            onManageWatchlist: _showWatchlistManager,
          ),
          _SignalsTab(
            loading: _loading,
            error: _error,
            scan: _scan,
            selectedItem: selectedItem,
            onRefresh: _loadInitial,
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tabIndex,
        onDestinationSelected: (index) => setState(() => _tabIndex = index),
        destinations: const [
          NavigationDestination(
              icon: Icon(Icons.candlestick_chart), label: '看盘'),
          NavigationDestination(
              icon: Icon(Icons.notifications_active), label: '信号'),
        ],
      ),
    );
  }

  void _showConnectionSheet() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) {
        return Padding(
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            bottom: MediaQuery.viewInsetsOf(context).bottom + 16,
          ),
          child: _ConnectionPanel(
            controller: _baseUrlController,
            loading: _loading,
            onConnect: () async {
              Navigator.of(context).pop();
              await _connect();
            },
            onRefresh: () async {
              Navigator.of(context).pop();
              await _loadInitial();
            },
          ),
        );
      },
    );
  }

  /// 弹出"管理品种"底部表单。
  void _showWatchlistManager() {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => WatchlistManagerSheet(
        initial: _watchlist,
        onSubmit: _updateWatchlist,
      ),
    );
  }
}

class _MarketTab extends StatelessWidget {
  const _MarketTab({
    required this.loading,
    required this.error,
    required this.status,
    required this.watchlist,
    required this.scan,
    required this.selectedItem,
    required this.selectedKey,
    required this.selectedTimeframe,
    required this.onRefresh,
    required this.onSelectedKey,
    required this.onTimeframeChanged,
    required this.onManageWatchlist,
  });

  final bool loading;
  final String? error;
  final JsonMap? status;
  final List<WatchItem> watchlist;
  final ScanPayload? scan;
  final ScanItem? selectedItem;
  final String? selectedKey;
  final String selectedTimeframe;
  final Future<void> Function() onRefresh;
  final ValueChanged<String> onSelectedKey;
  final ValueChanged<String> onTimeframeChanged;
  final VoidCallback onManageWatchlist;

  @override
  Widget build(BuildContext context) {
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(0, 0, 0, 92),
        children: [
          _MarketHeader(
            status: status,
            scan: scan,
            selectedItem: selectedItem,
          ),
          const SizedBox(height: 2),
          if (error != null) ...[
            _ErrorBanner(message: error!),
            const SizedBox(height: 2),
          ],
          _WatchlistStrip(
            watchlist: watchlist,
            scan: scan,
            selectedKey: selectedKey,
            onSelected: onSelectedKey,
            onManage: onManageWatchlist,
          ),
          const SizedBox(height: 2),
          if (loading && scan == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 80),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (selectedItem == null)
            _EmptyScan(message: scan?.message)
          else ...[
            PeriodPanel(
              item: selectedItem!,
              selectedTimeframe: selectedTimeframe,
              compact: true,
              onTimeframeChanged: onTimeframeChanged,
            ),
            const SizedBox(height: 2),
            KlineChart(
              item: selectedItem!,
              timeframe: selectedTimeframe,
              mobile: true,
            ),
          ],
        ],
      ),
    );
  }
}

class _SignalsTab extends StatelessWidget {
  const _SignalsTab({
    required this.loading,
    required this.error,
    required this.scan,
    required this.selectedItem,
    required this.onRefresh,
  });

  final bool loading;
  final String? error;
  final ScanPayload? scan;
  final ScanItem? selectedItem;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    final allSignals = scan?.signals ?? const <SignalItem>[];

    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
        children: [
          if (error != null) ...[
            _ErrorBanner(message: error!),
            const SizedBox(height: 10),
          ],
          if (loading && scan == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 80),
              child: Center(child: CircularProgressIndicator()),
            )
          else ...[
            SignalList(title: '全局信号', signals: allSignals),
            const SizedBox(height: 10),
            SignalList(
              title: selectedItem == null
                  ? '当前品种信号'
                  : '${selectedItem!.displayName} 信号',
              signals: selectedItem?.signals ?? const <SignalItem>[],
            ),
          ],
        ],
      ),
    );
  }
}

class _MarketHeader extends StatelessWidget {
  const _MarketHeader({
    required this.status,
    required this.scan,
    required this.selectedItem,
  });

  final JsonMap? status;
  final ScanPayload? scan;
  final ScanItem? selectedItem;

  @override
  Widget build(BuildContext context) {
    final connected = status != null;
    final trend = selectedItem?.entry ?? selectedItem?.trend;
    final title = selectedItem == null ? '等待数据' : selectedItem!.displayName;
    final subtitle = selectedItem == null
        ? '连接服务后点击右上角刷新'
        : '${selectedItem!.prefix}.${selectedItem!.exchange}  ${trend?.movementType ?? '结构待定'}';

    return Container(
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
      decoration: BoxDecoration(
        color: const Color(0xFF0B1220),
        border: Border(
          bottom: BorderSide(color: const Color(0xFF1F2937)),
        ),
      ),
      child: Row(
        children: [
          _Dot(
              color: connected
                  ? const Color(0xFF10B981)
                  : const Color(0xFFF59E0B)),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: Theme.of(context)
                      .textTheme
                      .titleMedium
                      ?.copyWith(fontWeight: FontWeight.w800, height: 1.05),
                ),
                Text(
                  scan?.scannedAt.isNotEmpty == true
                      ? '$subtitle · ${_shortTime(scan!.scannedAt)}'
                      : '$subtitle · 暂无数据',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: Colors.white54, fontSize: 11),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _ConnectionPanel extends StatelessWidget {
  const _ConnectionPanel({
    required this.controller,
    required this.loading,
    required this.onConnect,
    required this.onRefresh,
  });

  final TextEditingController controller;
  final bool loading;
  final VoidCallback onConnect;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('连接设置', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 12),
        TextField(
          controller: controller,
          keyboardType: TextInputType.url,
          textInputAction: TextInputAction.done,
          decoration: const InputDecoration(
            labelText: 'API 地址',
            hintText: AppConfig.defaultBaseUrl,
            isDense: true,
          ),
          onSubmitted: (_) {
            if (!loading) {
              onConnect();
            }
          },
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            FilledButton(
                onPressed: loading ? null : onConnect, child: const Text('连接')),
            OutlinedButton(
                onPressed: loading ? null : onRefresh, child: const Text('刷新')),
          ],
        ),
      ],
    );
  }
}

class _WatchlistStrip extends StatelessWidget {
  const _WatchlistStrip({
    required this.watchlist,
    required this.scan,
    required this.selectedKey,
    required this.onSelected,
    required this.onManage,
  });

  final List<WatchItem> watchlist;
  final ScanPayload? scan;
  final String? selectedKey;
  final ValueChanged<String> onSelected;
  final VoidCallback onManage;

  @override
  Widget build(BuildContext context) {
    // 后端扫描结果里 market+prefix+exchange 拼出来的 key,与前端 WatchItem.key 对齐。
    final scannedKeys = {
      for (final item in scan?.results ?? const <ScanItem>[])
        '${item.market.id}:${item.prefix}.${item.exchange}'
    };

    if (watchlist.isEmpty) {
      // 空状态时引导用户去添加,而不是干瘪的一行字。
      return Container(
        height: 36,
        color: const Color(0xFF0B1220),
        padding: const EdgeInsets.symmetric(horizontal: 10),
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          onPressed: onManage,
          icon: const Icon(Icons.add, size: 16),
          label: const Text('添加自选品种'),
          style: TextButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 6),
            minimumSize: const Size(0, 28),
            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          ),
        ),
      );
    }

    return Container(
      height: 36,
      color: const Color(0xFF0B1220),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
      child: Row(
        children: [
          Expanded(
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: watchlist.length,
              separatorBuilder: (context, index) => const SizedBox(width: 8),
              itemBuilder: (context, index) {
                final item = watchlist[index];
                final scanned = scannedKeys.contains(item.key);
                final marker = item.market == MarketType.stock ? '●' : '○';
                return ChoiceChip(
                  label: Text(
                      '$marker ${item.name} ${item.prefix}${scanned ? '' : ' · 未扫'}'),
                  selected: selectedKey == item.key,
                  onSelected: (_) => onSelected(item.key),
                  showCheckmark: false,
                  visualDensity:
                      const VisualDensity(horizontal: -4, vertical: -4),
                  materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  labelStyle: const TextStyle(
                      fontSize: 12, fontWeight: FontWeight.w700),
                  labelPadding: const EdgeInsets.symmetric(horizontal: 4),
                  padding: const EdgeInsets.symmetric(horizontal: 4),
                );
              },
            ),
          ),
          // 末尾常驻一个"管理"小按钮,方便随时改自选,不必去翻 AppBar。
          IconButton(
            tooltip: '管理品种',
            onPressed: onManage,
            iconSize: 18,
            padding: const EdgeInsets.symmetric(horizontal: 4),
            visualDensity: const VisualDensity(horizontal: -4, vertical: -4),
            icon: const Icon(Icons.tune),
          ),
        ],
      ),
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0x26F59E0B),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0x99F59E0B)),
      ),
      child: Text(message, style: const TextStyle(color: Color(0xFFFDE68A))),
    );
  }
}

class _EmptyScan extends StatelessWidget {
  const _EmptyScan({this.message});

  final String? message;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 42),
        child: Center(
          child: Text(
            message?.isNotEmpty == true ? message! : '暂无看盘结果，请点击右上角刷新读取最新数据',
            style: const TextStyle(color: Colors.white60),
            textAlign: TextAlign.center,
          ),
        ),
      ),
    );
  }
}

String _shortTime(String value) {
  if (value.length >= 16) {
    return value.substring(5, 16).replaceFirst('T', ' ');
  }
  return value;
}
