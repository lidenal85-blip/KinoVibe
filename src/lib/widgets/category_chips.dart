import 'package:flutter/material.dart';
import '../theme/abyssal_theme.dart';

const _categories = [
  ('movies', Icons.movie_rounded, 'Фильмы'),
  ('series', Icons.live_tv_rounded, 'Сериалы'),
  ('anime', Icons.auto_awesome_rounded, 'Аниме'),
  ('cartoons', Icons.child_care_rounded, 'Мультики'),
];

class CategoryChips extends StatelessWidget {
  final String selected;
  final ValueChanged<String> onSelect;

  const CategoryChips({
    super.key,
    required this.selected,
    required this.onSelect,
  });

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: _categories.map((cat) {
          final (id, icon, label) = cat;
          final isSelected = selected == id;
          return Padding(
            padding: const EdgeInsets.only(right: 8),
            child: _CategoryChip(
              id: id,
              icon: icon,
              label: label,
              isSelected: isSelected,
              onTap: () => onSelect(id),
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _CategoryChip extends StatefulWidget {
  final String id;
  final IconData icon;
  final String label;
  final bool isSelected;
  final VoidCallback onTap;

  const _CategoryChip({
    required this.id,
    required this.icon,
    required this.label,
    required this.isSelected,
    required this.onTap,
  });

  @override
  State<_CategoryChip> createState() => _CategoryChipState();
}

class _CategoryChipState extends State<_CategoryChip>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;
  late final Animation<double> _scale;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 150),
      value: widget.isSelected ? 1.0 : 0.0,
    );
    _scale = Tween<double>(begin: 0.95, end: 1.0).animate(
      CurvedAnimation(parent: _ctrl, curve: Curves.easeOut),
    );
  }

  @override
  void didUpdateWidget(_CategoryChip old) {
    super.didUpdateWidget(old);
    if (widget.isSelected != old.isSelected) {
      widget.isSelected ? _ctrl.forward() : _ctrl.reverse();
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ScaleTransition(
      scale: _scale,
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
          decoration: BoxDecoration(
            color: widget.isSelected
                ? AbyssalColors.cyan.withOpacity(0.15)
                : AbyssalColors.surface,
            borderRadius: BorderRadius.circular(12),
            border: Border.all(
              color: widget.isSelected
                  ? AbyssalColors.cyan.withOpacity(0.6)
                  : AbyssalColors.borderSubtle,
              width: 1,
            ),
            boxShadow: widget.isSelected
                ? AbyssalColors.cyanGlow(intensity: 0.5)
                : null,
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                widget.icon,
                size: 16,
                color: widget.isSelected
                    ? AbyssalColors.cyan
                    : AbyssalColors.textMuted,
              ),
              const SizedBox(width: 8),
              Text(
                widget.label,
                style: TextStyle(
                  fontSize: 13,
                  fontWeight:
                      widget.isSelected ? FontWeight.w700 : FontWeight.w500,
                  color: widget.isSelected
                      ? AbyssalColors.cyan
                      : AbyssalColors.textSecondary,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
