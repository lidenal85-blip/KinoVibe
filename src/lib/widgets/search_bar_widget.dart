import 'package:flutter/material.dart';
import '../theme/abyssal_theme.dart';

class AbyssalSearchBar extends StatefulWidget {
  final ValueChanged<String> onSearch;
  final String? initialValue;
  final bool isLoading;

  const AbyssalSearchBar({
    super.key,
    required this.onSearch,
    this.initialValue,
    this.isLoading = false,
  });

  @override
  State<AbyssalSearchBar> createState() => _AbyssalSearchBarState();
}

class _AbyssalSearchBarState extends State<AbyssalSearchBar>
    with SingleTickerProviderStateMixin {
  late final TextEditingController _ctrl;
  late final AnimationController _pulseCtrl;
  late final Animation<double> _pulseAnim;
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    _ctrl = TextEditingController(text: widget.initialValue);
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 2),
    )..repeat(reverse: true);
    _pulseAnim = Tween<double>(begin: 0.3, end: 0.7).animate(
      CurvedAnimation(parent: _pulseCtrl, curve: Curves.easeInOut),
    );
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _pulseCtrl.dispose();
    super.dispose();
  }

  void _submit() {
    final q = _ctrl.text.trim();
    if (q.isNotEmpty) widget.onSearch(q);
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _pulseAnim,
      builder: (context, child) => Container(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(16),
          boxShadow: _focused
              ? [
                  BoxShadow(
                    color: AbyssalColors.cyan.withOpacity(_pulseAnim.value),
                    blurRadius: 24,
                    spreadRadius: -4,
                  ),
                ]
              : [],
        ),
        child: child,
      ),
      child: Focus(
        onFocusChange: (f) => setState(() => _focused = f),
        child: TextField(
          controller: _ctrl,
          onSubmitted: (_) => _submit(),
          style: const TextStyle(
            color: AbyssalColors.textPrimary,
            fontSize: 16,
          ),
          decoration: InputDecoration(
            hintText: 'Найти фильм, сериал, аниме...',
            prefixIcon: const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16),
              child: Icon(Icons.search_rounded, color: AbyssalColors.cyan, size: 22),
            ),
            prefixIconConstraints: const BoxConstraints(minWidth: 56),
            suffixIcon: widget.isLoading
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : _ctrl.text.isNotEmpty
                    ? GestureDetector(
                        onTap: () {
                          _ctrl.clear();
                          setState(() {});
                        },
                        child: const Icon(
                          Icons.close_rounded,
                          color: AbyssalColors.textMuted,
                          size: 18,
                        ),
                      )
                    : null,
          ),
          onChanged: (_) => setState(() {}),
        ),
      ),
    );
  }
}
