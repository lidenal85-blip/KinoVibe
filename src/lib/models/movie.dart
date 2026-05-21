class Movie {
  final String title;
  final String? poster;
  final String? year;
  final String? rating;
  final String? description;
  final String url;
  final String provider;
  final String? category;
  // "video" | "embed" | "magnet" | "site"
  final String sourceType;

  const Movie({
    required this.title,
    this.poster,
    this.year,
    this.rating,
    this.description,
    required this.url,
    required this.provider,
    this.category,
    this.sourceType = 'video',
  });

  bool get isMagnet => url.startsWith('magnet:') || sourceType == 'magnet';
  bool get isSiteOnly => sourceType == 'site';

  factory Movie.fromJson(Map<String, dynamic> json) => Movie(
    title: json['title'] as String? ?? 'Без названия',
    poster: json['thumbnail'] as String? ?? json['poster'] as String?,
    year: json['year']?.toString(),
    rating: json['rating']?.toString(),
    description: json['description'] as String?,
    url: json['url'] as String? ?? '',
    provider: json['provider'] as String? ?? 'unknown',
    category: json['category'] as String?,
    sourceType: json['source_type'] as String? ?? 'video',
  );
}

class SearchResult {
  final List<Movie> items;
  final String? error;
  final int total;
  final String? mood;
  final String? genre;
  final String? language;

  const SearchResult({
    required this.items,
    this.error,
    this.total = 0,
    this.mood,
    this.genre,
    this.language,
  });

  factory SearchResult.fromJson(Map<String, dynamic> json) {
    final rawItems = json['results'] as List<dynamic>? ?? [];
    final meta = json['metadata'] as Map<String, dynamic>? ?? {};
    return SearchResult(
      items: rawItems.map((e) => Movie.fromJson(e as Map<String, dynamic>)).toList(),
      error: json['error'] as String?,
      total: json['count'] as int? ?? rawItems.length,
      mood: meta['mood'] as String?,
      genre: meta['genre'] as String?,
      language: meta['language'] as String?,
    );
  }
}

class Room {
  final String id;
  final String movieTitle;
  final String movieUrl;
  final int peers;
  final String? inviteUrl;

  const Room({
    required this.id,
    required this.movieTitle,
    required this.movieUrl,
    required this.peers,
    this.inviteUrl,
  });

  factory Room.fromJson(Map<String, dynamic> json) => Room(
    id: json['room_id'] as String? ?? json['id'] as String? ?? '',
    movieTitle: json['movie_title'] as String? ?? '',
    movieUrl: json['movie_url'] as String? ?? '',
    peers: json['peers'] as int? ?? 0,
    inviteUrl: json['invite_url'] as String?,
  );
}
