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
