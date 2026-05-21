import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:shimmer/shimmer.dart';
import '../models/movie.dart';
import '../services/api_service.dart';
import '../theme/abyssal_theme.dart';
import 'watch_screen.dart';

class ResultsScreen extends StatefulWidget {
  final SearchResult result;
  final String initialPlatform;
  final String query;

  const ResultsScreen({
    super.key,
    required this.result,
    required this.initialPlatform,
    required this.query,
  });

  @override
  State<ResultsScreen> createState() => _ResultsScreenState();
}

class _ResultsScreenState extends State<ResultsScreen> {
  late String _activePlatform;

  static const _platforms = [
    ('all', 'Все'),
    ('hdrezka', 'HDRezka'),
    ('youtube', 'YouTube'),
    ('torrent', 'Торренты'),
    ('vk', 'VK'),
    ('kodik', 'Кодик'),
  ];

  @override
  void initState() {
    super.initState();
    _activePlatform = 'all';
  }

  List<Movie> get _filtered {
    if (_activePlatform == 'all') return widget.result.items;
    return widget.result.items
        .where((m) => m.provider.toLowerCase() == _activePlatform)
        .toList();
  }

  void _openMovie(Movie movie) {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => WatchScreen(movie: movie)),
    );
  }

  Future<void> _createWatchParty(Movie movie) async {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => WatchScreen(movie: movie, createParty: true),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width > 900;
    final isMedium = MediaQuery.of(context).size.width > 600;
    final filtered = _filtered;

    return Scaffold(
      backgroundColor: AbyssalColors.background,
      appBar: AppBar(
        backgroundColor: AbyssalColors.surface,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded, color: AbyssalColors.cyan),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Результаты: "${widget.query}"',
              style: const TextStyle(
                  color: AbyssalColors.textPrimary,
                  fontSize: 14,
                  fontWeight: FontWeight.w700),
              overflow: TextOverflow.ellipsis,
            ),
            if (widget.result.mood != null || widget.result.genre != null)
              Text(
                [
                  if (widget.result.mood != null) 'Настроение: ${widget.result.mood}',
                  if (widget.result.genre != null) 'Жанр: ${widget.result.genre}',
                ].join(' · '),
                style: const TextStyle(
                    color: AbyssalColors.textMuted, fontSize: 11),
              ),
          ],
        ),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 16),
            child: Center(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                decoration: BoxDecoration(
                  color: AbyssalColors.cyan.withOpacity(0.1),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: AbyssalColors.cyan.withOpacity(0.4)),
                ),
                child: Text(
                  '${widget.result.total} результатов',
                  style: const TextStyle(
                      color: AbyssalColors.cyan,
                      fontSize: 11,
                      fontWeight: FontWeight.w700),
                ),
              ),
            ),
          ),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(49),
          child: _buildPlatformTabs(),
        ),
      ),
      body: filtered.isEmpty
          ? _buildEmpty()
          : Padding(
              padding: EdgeInsets.fromLTRB(
                isWide ? 32 : 16, 16, isWide ? 32 : 16, 32),
              child: GridView.builder(
                gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: isWide ? 6 : isMedium ? 4 : 2,
                  crossAxisSpacing: 14,
                  mainAxisSpacing: 14,
                  childAspectRatio: 0.62,
                ),
                itemCount: filtered.length,
                itemBuilder: (_, i) => _MovieCard(
                  movie: filtered[i],
                  onTap: () => _openMovie(filtered[i]),
                  onWatchParty: () => _createWatchParty(filtered[i]),
                ),
              ),
            ),
    );
  }

  Widget _buildPlatformTabs() {
    return Container(
      height: 49,
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: AbyssalColors.borderSubtle)),
      ),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        child: Row(
          children: _platforms.map((p) {
            final (id, label) = p;
            final sel = _activePlatform == id;
            final count = id == 'all'
                ? widget.result.items.length
                : widget.result.items
                    .where((m) => m.provider.toLowerCase() == id)
                    .length;
            return Padding(
              padding: const EdgeInsets.only(right: 8),
              child: GestureDetector(
                onTap: () => setState(() => _activePlatform = id),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 160),
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                  decoration: BoxDecoration(
                    color: sel ? AbyssalColors.cyan.withOpacity(0.15) : AbyssalColors.surface,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(
                      color: sel ? AbyssalColors.cyan.withOpacity(0.6) : AbyssalColors.borderSubtle,
                    ),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        label,
                        style: TextStyle(
                          fontSize: 12,
                          fontWeight: sel ? FontWeight.w700 : FontWeight.w500,
                          color: sel ? AbyssalColors.cyan : AbyssalColors.textSecondary,
                        ),
                      ),
                      if (count > 0) ...[
                        const SizedBox(width: 5),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                          decoration: BoxDecoration(
                            color: sel
                                ? AbyssalColors.cyan.withOpacity(0.2)
                                : AbyssalColors.surface,
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            '$count',
                            style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.w800,
                              color: sel ? AbyssalColors.cyan : AbyssalColors.textMuted,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
            );
          }).toList(),
        ),
      ),
    );
  }

  Widget _buildEmpty() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Container(
            width: 72,
            height: 72,
            decoration: BoxDecoration(
              color: AbyssalColors.surface,
              shape: BoxShape.circle,
              border: Border.all(color: AbyssalColors.borderSubtle),
            ),
            child: const Icon(Icons.search_off_rounded,
                size: 36, color: AbyssalColors.textMuted),
          ),
          const SizedBox(height: 16),
          const Text('Ничего не найдено',
              style: TextStyle(
                  color: AbyssalColors.textPrimary,
                  fontSize: 18,
                  fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          Text(
            _activePlatform == 'all'
                ? 'По вашему запросу результатов нет'
                : 'Нет результатов для выбранной платформы',
            style: const TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
          ),
        ],
      ),
    );
  }
}

// ─── Movie Card ───────────────────────────────────────────────────────────────

class _MovieCard extends StatefulWidget {
  final Movie movie;
  final VoidCallback? onTap;
  final VoidCallback? onWatchParty;

  const _MovieCard({required this.movie, this.onTap, this.onWatchParty});

  @override
  State<_MovieCard> createState() => _MovieCardState();
}

class _MovieCardState extends State<_MovieCard> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      cursor: SystemMouseCursors.click,
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          decoration: BoxDecoration(
            color: _hovered ? AbyssalColors.cardHover : AbyssalColors.card,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: _hovered ? AbyssalColors.borderActive : AbyssalColors.borderSubtle,
            ),
            boxShadow: _hovered
                ? AbyssalColors.cyanGlow(intensity: 0.8)
                : AbyssalColors.cardShadow,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(child: _buildPoster()),
              _buildInfo(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPoster() {
    return ClipRRect(
      borderRadius: const BorderRadius.vertical(top: Radius.circular(14)),
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (widget.movie.poster != null)
            CachedNetworkImage(
              imageUrl: ApiService.imageProxy(widget.movie.poster!),
              fit: BoxFit.cover,
              placeholder: (_, __) => Shimmer.fromColors(
                baseColor: AbyssalColors.surface,
                highlightColor: AbyssalColors.card,
                child: Container(color: AbyssalColors.surface),
              ),
              errorWidget: (_, __, ___) => _fallback(),
            )
          else
            _fallback(),
          // Gradient
          Positioned.fill(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  stops: const [0.5, 1.0],
                  colors: [Colors.transparent, AbyssalColors.card.withOpacity(0.95)],
                ),
              ),
            ),
          ),
          // Provider badge
          Positioned(
            top: 8,
            right: 8,
            child: _ProviderBadge(provider: widget.movie.provider),
          ),
          // Rating badge
          if (widget.movie.rating != null)
            Positioned(
              top: 8,
              left: 8,
              child: _RatingBadge(rating: widget.movie.rating!),
            ),
          // Play button on hover
          if (_hovered)
            Center(
              child: Container(
                width: 50,
                height: 50,
                decoration: BoxDecoration(
                  color: AbyssalColors.cyan.withOpacity(0.9),
                  shape: BoxShape.circle,
                  boxShadow: AbyssalColors.cyanGlow(intensity: 1.5),
                ),
                child: const Icon(Icons.play_arrow_rounded,
                    color: AbyssalColors.background, size: 28),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildInfo() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.movie.title,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
                color: AbyssalColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                height: 1.3),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: GestureDetector(
                  onTap: widget.onTap,
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 6),
                    decoration: BoxDecoration(
                      color: AbyssalColors.cyan.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(7),
                      border: Border.all(color: AbyssalColors.cyan.withOpacity(0.3)),
                    ),
                    child: const Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(Icons.play_circle_outline_rounded,
                            size: 14, color: AbyssalColors.cyan),
                        SizedBox(width: 5),
                        Text('Смотреть',
                            style: TextStyle(
                                color: AbyssalColors.cyan,
                                fontSize: 11,
                                fontWeight: FontWeight.w700)),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              GestureDetector(
                onTap: widget.onWatchParty,
                child: Tooltip(
                  message: 'Watch Party',
                  child: Container(
                    width: 32,
                    height: 32,
                    decoration: BoxDecoration(
                      color: AbyssalColors.violet.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(7),
                      border: Border.all(color: AbyssalColors.violet.withOpacity(0.4)),
                    ),
                    child: const Icon(Icons.group_rounded,
                        size: 16, color: AbyssalColors.violetGlow),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _fallback() => Container(
    color: AbyssalColors.surface,
    child: const Center(
      child: Icon(Icons.movie_outlined, size: 40, color: AbyssalColors.textMuted),
    ),
  );
}

// ─── Badges ───────────────────────────────────────────────────────────────────

class _ProviderBadge extends StatelessWidget {
  final String provider;

  const _ProviderBadge({required this.provider});

  static const _colors = {
    'youtube': Color(0xFFFF0000),
    'vk': Color(0xFF0077FF),
    'torrent': Color(0xFF00C853),
    'kodik': Color(0xFF7B2FBE),
    'hdrezka': Color(0xFFFF6B35),
  };

  @override
  Widget build(BuildContext context) {
    final color = _colors[provider.toLowerCase()] ?? AbyssalColors.textMuted;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: color.withOpacity(0.85),
        borderRadius: BorderRadius.circular(5),
      ),
      child: Text(
        provider.toUpperCase(),
        style: const TextStyle(
            fontSize: 8,
            fontWeight: FontWeight.w800,
            color: Colors.white,
            letterSpacing: 0.5),
      ),
    );
  }
}

class _RatingBadge extends StatelessWidget {
  final String rating;
  const _RatingBadge({required this.rating});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      decoration: BoxDecoration(
        color: AbyssalColors.background.withOpacity(0.8),
        borderRadius: BorderRadius.circular(5),
        border: Border.all(color: AbyssalColors.warning.withOpacity(0.6)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.star_rounded, size: 10, color: AbyssalColors.warning),
          const SizedBox(width: 3),
          Text(rating,
              style: const TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                  color: AbyssalColors.warning)),
        ],
      ),
    );
  }
}
