// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:shimmer/shimmer.dart';
import '../models/movie.dart';
import '../services/api_service.dart';
import '../theme/abyssal_theme.dart';
import 'results_screen.dart';
import 'watch_screen.dart';

class HubScreen extends StatefulWidget {
  const HubScreen({super.key});

  @override
  State<HubScreen> createState() => _HubScreenState();
}

class _HubScreenState extends State<HubScreen> {
  final _api = ApiService();
  final _ctrl = TextEditingController();
  final _focus = FocusNode();
  bool _loading = false;
  bool _vkLoggedIn = false;
  String _vkName = '';
  String _vkPhoto = '';
  String _category = 'movies';
  String _popularity = 'all';
  String _platform = 'all';
  String _mode = 'mood';

  // Sections data
  final Map<String, List<Movie>> _sectionMovies = {};
  final Map<String, bool> _sectionLoading = {};

  static const _sectionDefs = [
    (id: 'popular', label: 'Популярное сейчас', query: 'топ фильмы 2026', category: 'movies'),
    (id: 'comedy', label: 'Комедии', query: 'комедия фильм лучшие', category: 'movies'),
    (id: 'action', label: 'Боевики', query: 'боевик экшен фильм', category: 'movies'),
    (id: 'anime', label: 'Аниме', query: 'аниме лучшие', category: 'anime'),
  ];

  static const _categories = [
    ('movies', Icons.movie_rounded, 'Фильм'),
    ('series', Icons.live_tv_rounded, 'Сериал'),
    ('anime', Icons.auto_awesome_rounded, 'Аниме'),
    ('shorts', Icons.timer_outlined, 'Короткометражка'),
  ];

  static const _popularities = [
    ('rare', '💎', 'Редкие'),
    ('mid', '⭐', 'Средние'),
    ('mainstream', '🔥', 'Мейнстрим'),
  ];

  static const _platforms = [
    ('all', 'Все'),
    ('youtube', 'YouTube'),
    ('vk', 'VK'),
    ('torrent', 'Торренты'),
    ('kodik', 'Кодик'),
  ];

  @override
  void initState() {
    super.initState();
    _loadSections();
    _checkVkStatus();
    // Handle Watch Party deep link: /?room=ROOMCODE
    final uri = Uri.parse(html.window.location.href);
    final roomCode = uri.queryParameters['room'];
    if (roomCode != null && roomCode.isNotEmpty) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _joinRoom(roomCode));
    }
  }


  Future<void> _checkVkStatus() async {
    try {
      final s = await _api.vkStatus();
      if (mounted) setState(() {
        _vkLoggedIn = s['logged_in'] == true;
        _vkName  = (s['name']  ?? '') as String;
        _vkPhoto = (s['photo'] ?? '') as String;
      });
    } catch (_) {}
  }

  Future<void> _vkLogin() async {
    try {
      final origin = html.window.location.origin;
      final url = '$origin/kinovibe/vk-login.html';
      final popup = html.window.open(url, 'vk_oauth', 'width=480,height=560,scrollbars=yes');
      await for (final _ in Stream.periodic(const Duration(milliseconds: 600))) {
        if (popup.closed == true) break;
      }
      await _checkVkStatus();
      if (mounted && _vkLoggedIn) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text('Вошли как $_vkName'),
          backgroundColor: const Color(0xFF0077FF),
          duration: const Duration(seconds: 3),
        ));
      }
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('VK: $e'), backgroundColor: Colors.red));
    }
  }

  Future<void> _vkLogout() async {
    await _api.vkLogout();
    if (mounted) setState(() { _vkLoggedIn = false; _vkName = ''; _vkPhoto = ''; });
  }

  void _joinRoom(String code) {
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => WatchScreen(
        movie: Movie(title: 'Watch Party', url: '', provider: 'unknown'),
        roomId: code,
        isJoining: true,
      ),
    ));
  }

  void _loadSections() {
    for (final s in _sectionDefs) {
      setState(() => _sectionLoading[s.id] = true);
    }
    _api.fetchHome().then((home) {
      if (!mounted) return;
      setState(() {
        for (final s in _sectionDefs) {
          _sectionMovies[s.id] = home[s.id] ?? [];
          _sectionLoading[s.id] = false;
        }
      });
    }).catchError((e) {
      if (mounted) setState(() {
        for (final s in _sectionDefs) _sectionLoading[s.id] = false;
      });
    });
  }

  Future<void> _search() async {
    final query = _ctrl.text.trim();
    if (query.isEmpty) return;
    _focus.unfocus();
    setState(() => _loading = true);
    try {
      final result = await _api.search(
        query,
        category: _category,
        platform: _platform,
        popularity: _popularity,
        mode: _mode,
      );
      if (!mounted) return;
      setState(() => _loading = false);
      await Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => ResultsScreen(
            result: result,
            initialPlatform: _platform,
            query: query,
          ),
        ),
      );
    } catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Ошибка поиска: $e',
              style: const TextStyle(color: AbyssalColors.textPrimary)),
          backgroundColor: AbyssalColors.card,
        ),
      );
    }
  }

  void _openMovie(Movie movie) {
    Navigator.of(context).push(
      MaterialPageRoute(builder: (_) => WatchScreen(movie: movie)),
    );
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width > 700;

    return Scaffold(
      backgroundColor: AbyssalColors.background,
      body: CustomScrollView(
        slivers: [
          _buildAppBar(),
          // ── Search area ──────────────────────────────────────────────────
          SliverToBoxAdapter(
            child: Padding(
              padding: EdgeInsets.symmetric(
                horizontal: isWide ? 64 : 20,
                vertical: 32,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildLogo(),
                  const SizedBox(height: 32),
                  _buildModeToggle(),
                  const SizedBox(height: 16),
                  _buildQueryInput(),
                  const SizedBox(height: 24),
                  _buildSectionLabel('Что ищем?'),
                  const SizedBox(height: 12),
                  _buildCategoryButtons(),
                  const SizedBox(height: 20),
                  _buildSectionLabel('Популярность'),
                  const SizedBox(height: 12),
                  _buildPopularityButtons(),
                  const SizedBox(height: 20),
                  _buildSectionLabel('Платформа'),
                  const SizedBox(height: 12),
                  _buildPlatformTabs(),
                  const SizedBox(height: 32),
                  _buildSearchButton(),
                ],
              ),
            ),
          ),
          // ── Divider ──────────────────────────────────────────────────────
          const SliverToBoxAdapter(
            child: Divider(height: 1, color: AbyssalColors.borderSubtle),
          ),
          // ── Category sections ────────────────────────────────────────────
          for (final s in _sectionDefs) ...[
            SliverToBoxAdapter(
              child: _SectionRow(
                label: s.label,
                movies: _sectionMovies[s.id] ?? [],
                loading: _sectionLoading[s.id] ?? true,
                onTap: _openMovie,
                isWide: isWide,
              ),
            ),
          ],
          const SliverToBoxAdapter(child: SizedBox(height: 40)),
        ],
      ),
    );
  }

  Widget _buildAppBar() {
    return SliverAppBar(
      floating: true,
      snap: true,
      backgroundColor: AbyssalColors.surface,
      elevation: 0,
      expandedHeight: 56,
      flexibleSpace: FlexibleSpaceBar(
        titlePadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
        title: Row(
          children: [
            Container(
              width: 28,
              height: 28,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: const RadialGradient(
                  colors: [AbyssalColors.cyan, AbyssalColors.violet],
                ),
              ),
              child: const Icon(Icons.waves_rounded, size: 16, color: AbyssalColors.background),
            ),
            const SizedBox(width: 10),
            RichText(
              text: const TextSpan(children: [
                TextSpan(
                  text: 'KINO',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800,
                      color: AbyssalColors.textPrimary, letterSpacing: 1.5),
                ),
                TextSpan(
                  text: 'VIBE',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800,
                      color: AbyssalColors.cyan, letterSpacing: 1.5),
                ),
              ]),
            ),
            const Spacer(),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                // VK auth button
                GestureDetector(
                  onTap: _vkLoggedIn ? _vkLogout : _vkLogin,
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: _vkLoggedIn
                          ? const Color(0xFF0077FF).withOpacity(0.18)
                          : Colors.white.withOpacity(0.05),
                      borderRadius: BorderRadius.circular(20),
                      border: Border.all(color: _vkLoggedIn
                          ? const Color(0xFF0077FF).withOpacity(0.6)
                          : Colors.white.withOpacity(0.15)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (_vkLoggedIn && _vkPhoto.isNotEmpty) ...[
                          ClipOval(child: Image.network(_vkPhoto,
                              width: 18, height: 18, fit: BoxFit.cover)),
                          const SizedBox(width: 5),
                          Text(_vkName.split(' ').first,
                              style: const TextStyle(fontSize: 10,
                                  fontWeight: FontWeight.w600,
                                  color: Color(0xFF4D9FFF))),
                        ] else ...[
                          const Icon(Icons.person_rounded, size: 14,
                              color: Color(0xFF0077FF)),
                          const SizedBox(width: 4),
                          Text(_vkLoggedIn ? _vkName.split(' ').first : 'VK',
                              style: TextStyle(fontSize: 10,
                                  fontWeight: FontWeight.w700,
                                  color: _vkLoggedIn
                                      ? const Color(0xFF4D9FFF)
                                      : Colors.white54,
                                  letterSpacing: 0.3)),
                        ],
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                // ONLINE badge
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: AbyssalColors.success.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: AbyssalColors.success.withOpacity(0.4)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(width: 6, height: 6,
                          decoration: const BoxDecoration(
                              color: AbyssalColors.success,
                              shape: BoxShape.circle)),
                      const SizedBox(width: 5),
                      const Text('ONLINE', style: TextStyle(fontSize: 10,
                          fontWeight: FontWeight.w700,
                          color: AbyssalColors.success, letterSpacing: 0.5)),
                    ],
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
      bottom: const PreferredSize(
        preferredSize: Size.fromHeight(1),
        child: Divider(height: 1, color: AbyssalColors.borderSubtle),
      ),
    );
  }

  Widget _buildLogo() {
    return Center(
      child: Column(
        children: [
          Container(
            width: 80,
            height: 80,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              gradient: RadialGradient(colors: [
                AbyssalColors.cyan.withOpacity(0.2),
                AbyssalColors.violet.withOpacity(0.1),
                Colors.transparent,
              ]),
              border: Border.all(color: AbyssalColors.cyan.withOpacity(0.3), width: 1.5),
              boxShadow: AbyssalColors.cyanGlow(intensity: 0.6),
            ),
            child: const Icon(Icons.waves_rounded, size: 40, color: AbyssalColors.cyan),
          ),
          const SizedBox(height: 16),
          const Text(
            'Что смотрим сегодня?',
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800,
                color: AbyssalColors.textPrimary),
          ),
          const SizedBox(height: 6),
          const Text(
            'Ищи по названию или опиши настроение',
            style: TextStyle(fontSize: 13, color: AbyssalColors.textSecondary),
          ),
        ],
      ),
    );
  }

  Widget _buildModeToggle() {
    final isMood = _mode == 'mood';
    return Container(
      decoration: BoxDecoration(
        color: AbyssalColors.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AbyssalColors.borderSubtle),
      ),
      padding: const EdgeInsets.all(4),
      child: Row(
        children: [
          _ModeTab(
            label: 'Поиск',
            icon: Icons.search_rounded,
            active: !isMood,
            activeColor: AbyssalColors.violet,
            onTap: () => setState(() => _mode = 'search'),
          ),
          _ModeTab(
            label: 'Настроение',
            icon: Icons.auto_awesome_rounded,
            active: isMood,
            activeColor: AbyssalColors.cyan,
            onTap: () => setState(() => _mode = 'mood'),
          ),
        ],
      ),
    );
  }

  Widget _buildQueryInput() {
    final isMood = _mode == 'mood';
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        boxShadow: AbyssalColors.cyanGlow(intensity: 0.4),
      ),
      child: TextField(
        controller: _ctrl,
        focusNode: _focus,
        maxLines: isMood ? 3 : 1,
        minLines: isMood ? 3 : 1,
        style: const TextStyle(color: AbyssalColors.textPrimary, fontSize: 15),
        onSubmitted: (_) => _search(),
        decoration: InputDecoration(
          hintText: isMood
              ? 'Опиши что хочешь посмотреть...'
              : 'Найди фильм или сериал...',
          hintStyle: const TextStyle(color: AbyssalColors.textMuted, fontSize: 14, height: 1.5),
          prefixIcon: Padding(
            padding: EdgeInsets.fromLTRB(16, isMood ? 14 : 0, 8, 0),
            child: Icon(
              isMood ? Icons.auto_awesome_rounded : Icons.search_rounded,
              color: isMood ? AbyssalColors.cyan : AbyssalColors.violet,
              size: 22,
            ),
          ),
          prefixIconConstraints: const BoxConstraints(minWidth: 52, minHeight: 0),
          contentPadding: const EdgeInsets.fromLTRB(8, 16, 16, 16),
        ),
      ),
    );
  }

  Widget _buildSectionLabel(String text) {
    return Text(
      text,
      style: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w700,
        color: AbyssalColors.textMuted,
        letterSpacing: 1.0,
      ),
    );
  }

  Widget _buildCategoryButtons() {
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: _categories.map((cat) {
        final (id, icon, label) = cat;
        final sel = _category == id;
        return _FilterChip(
          label: label,
          icon: icon,
          selected: sel,
          onTap: () => setState(() => _category = id),
          activeColor: AbyssalColors.cyan,
        );
      }).toList(),
    );
  }

  Widget _buildPopularityButtons() {
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: _popularities.map((p) {
        final (id, emoji, label) = p;
        final sel = _popularity == id;
        return _EmojiFilterChip(
          emoji: emoji,
          label: label,
          selected: sel,
          onTap: () => setState(() => _popularity == id ? _popularity = 'all' : _popularity = id),
          activeColor: AbyssalColors.violet,
        );
      }).toList(),
    );
  }

  Widget _buildPlatformTabs() {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: _platforms.map((p) {
          final (id, label) = p;
          final sel = _platform == id;
          return Padding(
            padding: const EdgeInsets.only(right: 8),
            child: GestureDetector(
              onTap: () => setState(() => _platform = id),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 180),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
                decoration: BoxDecoration(
                  color: sel ? AbyssalColors.cyan.withOpacity(0.15) : AbyssalColors.surface,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(
                    color: sel ? AbyssalColors.cyan.withOpacity(0.6) : AbyssalColors.borderSubtle,
                  ),
                ),
                child: Text(
                  label,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: sel ? FontWeight.w700 : FontWeight.w500,
                    color: sel ? AbyssalColors.cyan : AbyssalColors.textSecondary,
                  ),
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildSearchButton() {
    return SizedBox(
      width: double.infinity,
      child: ElevatedButton(
        onPressed: _loading ? null : _search,
        style: ElevatedButton.styleFrom(
          padding: const EdgeInsets.symmetric(vertical: 18),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        ),
        child: _loading
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                  color: AbyssalColors.background,
                  strokeWidth: 2.5,
                ),
              )
            : const Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(Icons.search_rounded, size: 20),
                  SizedBox(width: 10),
                  Text('Найти', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
                ],
              ),
      ),
    );
  }
}

// ─── Section Row ──────────────────────────────────────────────────────────────

class _SectionRow extends StatelessWidget {
  final String label;
  final List<Movie> movies;
  final bool loading;
  final void Function(Movie) onTap;
  final bool isWide;

  const _SectionRow({
    required this.label,
    required this.movies,
    required this.loading,
    required this.onTap,
    required this.isWide,
  });

  @override
  Widget build(BuildContext context) {
    final hPad = isWide ? 64.0 : 20.0;
    return Padding(
      padding: EdgeInsets.only(top: 28, bottom: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.symmetric(horizontal: hPad),
            child: Row(
              children: [
                Container(
                  width: 3,
                  height: 18,
                  decoration: BoxDecoration(
                    color: AbyssalColors.cyan,
                    borderRadius: BorderRadius.circular(2),
                    boxShadow: AbyssalColors.cyanGlow(intensity: 0.8),
                  ),
                ),
                const SizedBox(width: 10),
                Text(
                  label,
                  style: const TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w800,
                    color: AbyssalColors.textPrimary,
                    letterSpacing: 0.3,
                  ),
                ),
                const Spacer(),
                if (!loading && movies.isNotEmpty)
                  Text(
                    '${movies.length}',
                    style: const TextStyle(
                      fontSize: 12,
                      color: AbyssalColors.textMuted,
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 14),
          SizedBox(
            height: 220,
            child: loading
                ? _ShimmerRow(hPad: hPad)
                : movies.isEmpty
                    ? Padding(
                        padding: EdgeInsets.symmetric(horizontal: hPad),
                        child: const Center(
                          child: Text(
                            'Нет результатов',
                            style: TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
                          ),
                        ),
                      )
                    : ListView.builder(
                        scrollDirection: Axis.horizontal,
                        padding: EdgeInsets.symmetric(horizontal: hPad),
                        itemCount: movies.length,
                        itemBuilder: (_, i) => _MiniCard(
                          movie: movies[i],
                          onTap: () => onTap(movies[i]),
                        ),
                      ),
          ),
        ],
      ),
    );
  }
}

// ─── Mini Card ────────────────────────────────────────────────────────────────

class _MiniCard extends StatefulWidget {
  final Movie movie;
  final VoidCallback onTap;

  const _MiniCard({required this.movie, required this.onTap});

  @override
  State<_MiniCard> createState() => _MiniCardState();
}

class _MiniCardState extends State<_MiniCard> {
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
          duration: const Duration(milliseconds: 180),
          width: 130,
          margin: const EdgeInsets.only(right: 12),
          decoration: BoxDecoration(
            color: _hovered ? AbyssalColors.cardHover : AbyssalColors.card,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: _hovered ? AbyssalColors.borderActive : AbyssalColors.borderSubtle,
            ),
            boxShadow: _hovered ? AbyssalColors.cyanGlow(intensity: 0.6) : AbyssalColors.cardShadow,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Poster
              Expanded(
                child: ClipRRect(
                  borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      if (widget.movie.poster != null)
                        CachedNetworkImage(
                          imageUrl: widget.movie.poster!,
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
                      if (_hovered)
                        Container(
                          color: AbyssalColors.background.withOpacity(0.35),
                          child: const Center(
                            child: Icon(Icons.play_circle_outline_rounded,
                                size: 36, color: AbyssalColors.cyan),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              // Title
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 6, 8, 8),
                child: Text(
                  widget.movie.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: AbyssalColors.textPrimary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    height: 1.3,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _fallback() => Container(
    color: AbyssalColors.surface,
    child: const Center(
      child: Icon(Icons.movie_outlined, size: 32, color: AbyssalColors.textMuted),
    ),
  );
}

// ─── Shimmer Row ──────────────────────────────────────────────────────────────

class _ShimmerRow extends StatelessWidget {
  final double hPad;
  const _ShimmerRow({required this.hPad});

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      scrollDirection: Axis.horizontal,
      padding: EdgeInsets.symmetric(horizontal: hPad),
      itemCount: 6,
      itemBuilder: (_, __) => Shimmer.fromColors(
        baseColor: AbyssalColors.surface,
        highlightColor: AbyssalColors.card,
        child: Container(
          width: 130,
          margin: const EdgeInsets.only(right: 12),
          decoration: BoxDecoration(
            color: AbyssalColors.surface,
            borderRadius: BorderRadius.circular(12),
          ),
        ),
      ),
    );
  }
}

// ─── Filter Chips ─────────────────────────────────────────────────────────────

class _FilterChip extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;
  final Color activeColor;

  const _FilterChip({
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
    required this.activeColor,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? activeColor.withOpacity(0.15) : AbyssalColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? activeColor.withOpacity(0.6) : AbyssalColors.borderSubtle,
          ),
          boxShadow: selected ? [
            BoxShadow(color: activeColor.withOpacity(0.15), blurRadius: 12),
          ] : null,
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16,
                color: selected ? activeColor : AbyssalColors.textMuted),
            const SizedBox(width: 8),
            Text(
              label,
              style: TextStyle(
                fontSize: 13,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                color: selected ? activeColor : AbyssalColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ModeTab extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool active;
  final Color activeColor;
  final VoidCallback onTap;

  const _ModeTab({
    required this.label,
    required this.icon,
    required this.active,
    required this.activeColor,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: GestureDetector(
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            color: active ? activeColor.withOpacity(0.15) : Colors.transparent,
            borderRadius: BorderRadius.circular(9),
            border: active
                ? Border.all(color: activeColor.withOpacity(0.5))
                : Border.all(color: Colors.transparent),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 16, color: active ? activeColor : AbyssalColors.textMuted),
              const SizedBox(width: 6),
              Text(
                label,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: active ? FontWeight.w700 : FontWeight.w500,
                  color: active ? activeColor : AbyssalColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _EmojiFilterChip extends StatelessWidget {
  final String emoji;
  final String label;
  final bool selected;
  final VoidCallback onTap;
  final Color activeColor;

  const _EmojiFilterChip({
    required this.emoji,
    required this.label,
    required this.selected,
    required this.onTap,
    required this.activeColor,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 180),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: BoxDecoration(
          color: selected ? activeColor.withOpacity(0.15) : AbyssalColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? activeColor.withOpacity(0.6) : AbyssalColors.borderSubtle,
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(emoji, style: const TextStyle(fontSize: 16)),
            const SizedBox(width: 8),
            Text(
              label,
              style: TextStyle(
                fontSize: 13,
                fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
                color: selected ? activeColor : AbyssalColors.textSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
