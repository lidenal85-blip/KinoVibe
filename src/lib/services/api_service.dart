import 'dart:convert';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:http/http.dart' as http;
import '../models/movie.dart';

class ApiService {
  static String get _base => '${html.window.location.origin}/api';

  static String imageProxy(String url) =>
      '$_base/image-proxy?url=${Uri.encodeComponent(url)}';

  Future<SearchResult> search(
    String query, {
    String category = 'movies',
    String platform = 'all',
    String popularity = 'all',
    String mode = 'mood',
  }) async {
    final res = await http.post(
      Uri.parse('$_base/search'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'query': query,
        'category': category,
        'platform': platform,
        'popularity': popularity,
        'mode': mode,
      }),
    ).timeout(const Duration(seconds: 60));

    if (res.statusCode != 200) {
      throw Exception('Search failed: ${res.statusCode}');
    }
    return SearchResult.fromJson(jsonDecode(res.body) as Map<String, dynamic>);
  }



  Future<List<Map<String,dynamic>>> filmStreams(String title, {int? year, String category='movies'}) async {
    final res = await http.post(
      Uri.parse('$_base/film/streams'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'title': title, 'year': year, 'category': category}),
    ).timeout(const Duration(seconds: 25));
    if (res.statusCode != 200) return [];
    final d = jsonDecode(res.body) as Map;
    return List<Map<String,dynamic>>.from(d['streams'] ?? []);
  }
  Future<Map<String, List<Movie>>> fetchHome() async {
    final res = await http.get(Uri.parse('$_base/home')).timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) return {};
    final d = jsonDecode(res.body) as Map<String, dynamic>;
    return d.map((k, v) => MapEntry(k, (v as List).map((e) => Movie.fromJson(e)).toList()));
  }
  Future<Map<String, dynamic>> getStream(String url, {String? provider}) async {
    final params = {'url': url, if (provider != null) 'provider': provider};
    final res = await http.get(
      Uri.parse('$_base/stream').replace(queryParameters: params),
    ).timeout(const Duration(seconds: 60));

    if (res.statusCode != 200) {
      throw Exception('Stream failed: ${res.statusCode}');
    }
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> createRoom({
    String movieUrl = '',
    String movieTitle = '',
  }) async {
    final res = await http.post(
      Uri.parse('$_base/rooms/create'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'movie_url': movieUrl, 'movie_title': movieTitle}),
    ).timeout(const Duration(seconds: 10));

    if (res.statusCode != 200) throw Exception('Create room failed');
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> hlsStart(String url) async {
    final res = await http.post(
      Uri.parse('$_base/hls/start'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'url': url}),
    ).timeout(const Duration(seconds: 30));
    if (res.statusCode != 200) throw Exception('HLS start failed: ${res.statusCode}');
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> hlsStatus(String streamId) async {
    final res = await http.get(
      Uri.parse('$_base/hls/$streamId/status'),
    ).timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) throw Exception('HLS status failed: ${res.statusCode}');
    return jsonDecode(res.body) as Map<String, dynamic>;
  }


  // ── VK Auth ─────────────────────────────────────────────────────────────

  Future<String> vkAuthUrl() async {
    final origin = html.window.location.origin;
    final callback = '$origin/vk-callback';
    final res = await http.get(
      Uri.parse('$_base/vk/auth-url?redirect_uri=${Uri.encodeComponent(callback)}'),
    ).timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) throw Exception('VK auth-url failed');
    return (jsonDecode(res.body) as Map)['url'] as String;
  }

  Future<Map<String, dynamic>> vkSetToken(String token, int userId) async {
    final res = await http.post(
      Uri.parse('$_base/vk/set-token'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'access_token': token, 'user_id': userId}),
    ).timeout(const Duration(seconds: 15));
    if (res.statusCode != 200) throw Exception('VK set-token: ${res.statusCode}');
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> vkStatus() async {
    final res = await http.get(Uri.parse('$_base/vk/status'))
        .timeout(const Duration(seconds: 10));
    if (res.statusCode != 200) return {'logged_in': false};
    return jsonDecode(res.body) as Map<String, dynamic>;
  }

  Future<void> vkLogout() async {
    await http.post(Uri.parse('$_base/vk/logout'))
        .timeout(const Duration(seconds: 10));
  }
  Future<List<Room>> listRooms() async {
    final res = await http.get(Uri.parse('$_base/rooms'))
        .timeout(const Duration(seconds: 10));

    if (res.statusCode != 200) return [];
    final data = jsonDecode(res.body);
    if (data is List) {
      return data.map((e) => Room.fromJson(e as Map<String, dynamic>)).toList();
    }
    return [];
  }
}
