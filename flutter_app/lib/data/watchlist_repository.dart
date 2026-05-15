import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../models/market_models.dart';

/// 本地自选品种仓库 — 用 SharedPreferences 持久化用户在前端添加的品种。
///
/// 设计目的:
/// - 每台手机各管各的品种,不污染后端 config.yaml
/// - 多人 TestFlight 测试时互不干扰
/// - 启动时如果本地为空,会用后端 /api/watchlist 拿到的种子列表回填
class WatchlistRepository {
  static const _storageKey = 'finme.watchlist.v1';

  /// 读取本地保存的 watchlist。第一次启动返回空列表。
  Future<List<WatchItem>> load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_storageKey);
    if (raw == null || raw.isEmpty) return const [];

    try {
      final decoded = jsonDecode(raw);
      if (decoded is! List) return const [];
      return decoded
          .whereType<Map>()
          .map((e) => WatchItem.fromJson(asJsonMap(e)))
          .toList();
    } catch (_) {
      // 损坏的存储数据 — 直接当空,避免阻塞启动。
      return const [];
    }
  }

  /// 全量覆盖保存。所有 add/remove 最终都走这里。
  Future<void> save(List<WatchItem> items) async {
    final prefs = await SharedPreferences.getInstance();
    final encoded = jsonEncode(items.map((w) => w.toJson()).toList());
    await prefs.setString(_storageKey, encoded);
  }

  /// 清空本地存储 — 调试 / 重置时用。
  Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_storageKey);
  }
}
