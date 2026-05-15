import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../config/app_config.dart';
import '../models/market_models.dart';

class FinmeApiException implements Exception {
  const FinmeApiException(this.message);

  final String message;

  @override
  String toString() => message;
}

class FinmeApi {
  FinmeApi(String baseUrl) : baseUri = Uri.parse(_normalizeBaseUrl(baseUrl));

  final Uri baseUri;

  String get baseUrl => baseUri.toString();

  Future<JsonMap> fetchStatus() {
    return _get('/api/status');
  }

  Future<WatchlistResponse> fetchWatchlist() async {
    final json = await _get('/api/watchlist');
    return WatchlistResponse.fromJson(json);
  }

  Future<ScanPayload> fetchLastScan() async {
    final json = await _get('/api/signals/last');
    return ScanPayload.fromJson(json);
  }

  Future<ScanPayload> triggerScan() async {
    final json =
        await _post('/api/signals/scan', timeout: AppConfig.scanTimeout);
    return ScanPayload.fromJson(json);
  }

  Future<JsonMap> _get(String path, {Duration? timeout}) async {
    final response = await http
        .get(_resolve(path))
        .timeout(timeout ?? AppConfig.requestTimeout);
    return _decodeResponse(response);
  }

  Future<JsonMap> _post(String path, {Duration? timeout}) async {
    final response = await http
        .post(_resolve(path))
        .timeout(timeout ?? AppConfig.requestTimeout);
    return _decodeResponse(response);
  }

  Uri _resolve(String path) {
    final normalizedPath = path.startsWith('/') ? path.substring(1) : path;
    return baseUri.replace(path: _joinPaths(baseUri.path, normalizedPath));
  }

  JsonMap _decodeResponse(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw FinmeApiException('HTTP ${response.statusCode}: ${response.body}');
    }

    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map) {
      throw const FinmeApiException('接口返回的不是 JSON 对象');
    }
    return asJsonMap(decoded);
  }

  static String _normalizeBaseUrl(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) {
      return AppConfig.defaultBaseUrl;
    }
    return trimmed.endsWith('/')
        ? trimmed.substring(0, trimmed.length - 1)
        : trimmed;
  }

  static String _joinPaths(String basePath, String path) {
    final prefix = basePath.endsWith('/')
        ? basePath.substring(0, basePath.length - 1)
        : basePath;
    if (prefix.isEmpty) {
      return '/$path';
    }
    return '$prefix/$path';
  }
}
