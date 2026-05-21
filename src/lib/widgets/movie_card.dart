import 'package:flutter/material.dart';
import 'package:cached_network_image/cached_network_image.dart';
import 'package:shimmer/shimmer.dart';
import '../models/movie.dart';
import '../theme/abyssal_theme.dart';

class MovieCard extends StatefulWidget {
  final Movie movie;
  final VoidCallback? onTap;
  final VoidCallback? onWatchParty;

  const MovieCard({
    super.key,
    required this.movie,
    this.onTap,
    this.onWatchParty,
  });

  @override
  State<MovieCard> createState() => _MovieCardState();
}

class _MovieCardState extends State<MovieCard> {
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
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: _hovered
                  ? AbyssalColors.borderActive
                  : AbyssalColors.borderSubtle,
              width: 1,
            ),
            boxShadow: _hovered
                ? AbyssalColors.cyanGlow(intensity: 0.8)
                : AbyssalColors.cardShadow,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _PosterSection(movie: widget.movie, hovered: _hovered),
              _InfoSection(
                movie: widget.movie,
                onWatchParty: widget.onWatchParty,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PosterSection extends StatelessWidget {
  final Movie movie;
  final bool hovered;

  const _PosterSection({required this.movie, required this.hovered});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: const BorderRadius.vertical(top: Radius.circular(16)),
      child: AspectRatio(
        aspectRatio: 2 / 3,
        child: Stack(
          fit: StackFit.expand,
          children: [
            if (movie.poster != null)
              CachedNetworkImage(
                imageUrl: movie.poster!,
                fit: BoxFit.cover,
                placeholder: (_, __) => _shimmerPlaceholder(),
                errorWidget: (_, __, ___) => _fallbackPoster(),
              )
            else
              _fallbackPoster(),
            // Gradient overlay
            Positioned.fill(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    stops: const [0.5, 1.0],
                    colors: [
                      Colors.transparent,
                      AbyssalColors.card.withOpacity(0.95),
                    ],
                  ),
                ),
              ),
            ),
            // Provider badge
            Positioned(
              top: 10,
              right: 10,
              child: _ProviderBadge(provider: movie.provider),
            ),
            // Rating
            if (movie.rating != null)
              Positioned(
                top: 10,
                left: 10,
                child: _RatingBadge(rating: movie.rating!),
              ),
            // Hover play button
            if (hovered)
              Center(
                child: Container(
                  width: 56,
                  height: 56,
                  decoration: BoxDecoration(
                    color: AbyssalColors.cyan.withOpacity(0.9),
                    shape: BoxShape.circle,
                    boxShadow: AbyssalColors.cyanGlow(intensity: 1.5),
                  ),
                  child: const Icon(
                    Icons.play_arrow_rounded,
                    color: AbyssalColors.background,
                    size: 32,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Widget _fallbackPoster() => Container(
    color: AbyssalColors.surface,
    child: const Center(
      child: Icon(Icons.movie_outlined, size: 48, color: AbyssalColors.textMuted),
    ),
  );

  Widget _shimmerPlaceholder() => Shimmer.fromColors(
    baseColor: AbyssalColors.surface,
    highlightColor: AbyssalColors.card,
    child: Container(color: AbyssalColors.surface),
  );
}

class _InfoSection extends StatelessWidget {
  final Movie movie;
  final VoidCallback? onWatchParty;

  const _InfoSection({required this.movie, this.onWatchParty});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            movie.title,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.titleMedium,
          ),
          if (movie.year != null) ...[
            const SizedBox(height: 4),
            Text(
              movie.year!,
              style: Theme.of(context).textTheme.bodyMedium,
            ),
          ],
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: _GlowButton(
                  label: 'Смотреть',
                  icon: Icons.play_circle_outline_rounded,
                  onTap: null,
                ),
              ),
              const SizedBox(width: 8),
              _IconGlowButton(
                icon: Icons.group_rounded,
                tooltip: 'Вечеринка',
                onTap: onWatchParty,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _GlowButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final VoidCallback? onTap;

  const _GlowButton({required this.label, required this.icon, this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8),
        decoration: BoxDecoration(
          color: AbyssalColors.cyan.withOpacity(0.12),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: AbyssalColors.cyan.withOpacity(0.3)),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(icon, size: 16, color: AbyssalColors.cyan),
            const SizedBox(width: 6),
            Text(
              label,
              style: const TextStyle(
                color: AbyssalColors.cyan,
                fontSize: 12,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _IconGlowButton extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback? onTap;

  const _IconGlowButton({required this.icon, required this.tooltip, this.onTap});

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: GestureDetector(
        onTap: onTap,
        child: Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(
            color: AbyssalColors.violet.withOpacity(0.15),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: AbyssalColors.violet.withOpacity(0.4)),
          ),
          child: Icon(icon, size: 18, color: AbyssalColors.violetGlow),
        ),
      ),
    );
  }
}

class _ProviderBadge extends StatelessWidget {
  final String provider;

  const _ProviderBadge({required this.provider});

  static const _colors = {
    'youtube': Color(0xFFFF0000),
    'vk': Color(0xFF0077FF),
    'torrent': Color(0xFF00C853),
    'kodik': Color(0xFF7B2FBE),
  };

  @override
  Widget build(BuildContext context) {
    final color = _colors[provider.toLowerCase()] ?? AbyssalColors.textMuted;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withOpacity(0.85),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(
        provider.toUpperCase(),
        style: const TextStyle(
          fontSize: 9,
          fontWeight: FontWeight.w800,
          color: Colors.white,
          letterSpacing: 0.5,
        ),
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
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: AbyssalColors.background.withOpacity(0.8),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AbyssalColors.warning.withOpacity(0.6)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.star_rounded, size: 11, color: AbyssalColors.warning),
          const SizedBox(width: 3),
          Text(
            rating,
            style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w700,
              color: AbyssalColors.warning,
            ),
          ),
        ],
      ),
    );
  }
}
