import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

class AbyssalColors {
  // Backgrounds
  static const background = Color(0xFF050A14);
  static const surface = Color(0xFF0A1628);
  static const card = Color(0xFF0D1F3C);
  static const cardHover = Color(0xFF122540);

  // Accents
  static const cyan = Color(0xFF00D4FF);
  static const cyanDim = Color(0xFF0099BB);
  static const violet = Color(0xFF7B2FBE);
  static const violetGlow = Color(0xFF9B4FDE);

  // Text
  static const textPrimary = Color(0xFFE8F4FD);
  static const textSecondary = Color(0xFF6B8CAE);
  static const textMuted = Color(0xFF3A5A7C);

  // Borders & dividers
  static const borderSubtle = Color(0x26007799);  // cyan 15%
  static const borderActive = Color(0x66007799);  // cyan 40%

  // Status
  static const success = Color(0xFF00E5A0);
  static const error = Color(0xFFFF3B5C);
  static const warning = Color(0xFFFFB020);

  // Glow shadows
  static List<BoxShadow> cyanGlow({double intensity = 1.0}) => [
    BoxShadow(
      color: cyan.withOpacity(0.15 * intensity),
      blurRadius: 20 * intensity,
      spreadRadius: 0,
    ),
    BoxShadow(
      color: cyan.withOpacity(0.08 * intensity),
      blurRadius: 40 * intensity,
      spreadRadius: 5,
    ),
  ];

  static List<BoxShadow> cardShadow = [
    BoxShadow(
      color: Colors.black.withOpacity(0.5),
      blurRadius: 16,
      offset: const Offset(0, 8),
    ),
    BoxShadow(
      color: cyan.withOpacity(0.05),
      blurRadius: 24,
      spreadRadius: -4,
    ),
  ];
}

class AbyssalTheme {
  static ThemeData get theme => ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    scaffoldBackgroundColor: AbyssalColors.background,
    colorScheme: const ColorScheme.dark(
      background: AbyssalColors.background,
      surface: AbyssalColors.surface,
      primary: AbyssalColors.cyan,
      secondary: AbyssalColors.violet,
      onBackground: AbyssalColors.textPrimary,
      onSurface: AbyssalColors.textPrimary,
      onPrimary: AbyssalColors.background,
      error: AbyssalColors.error,
    ),
    textTheme: GoogleFonts.nunitoTextTheme(
      const TextTheme(
        displayLarge: TextStyle(
          fontSize: 32,
          fontWeight: FontWeight.w800,
          color: AbyssalColors.textPrimary,
          letterSpacing: -0.5,
        ),
        displayMedium: TextStyle(
          fontSize: 24,
          fontWeight: FontWeight.w700,
          color: AbyssalColors.textPrimary,
          letterSpacing: -0.3,
        ),
        titleLarge: TextStyle(
          fontSize: 20,
          fontWeight: FontWeight.w700,
          color: AbyssalColors.textPrimary,
        ),
        titleMedium: TextStyle(
          fontSize: 16,
          fontWeight: FontWeight.w600,
          color: AbyssalColors.textPrimary,
        ),
        bodyLarge: TextStyle(
          fontSize: 15,
          color: AbyssalColors.textPrimary,
          height: 1.5,
        ),
        bodyMedium: TextStyle(
          fontSize: 13,
          color: AbyssalColors.textSecondary,
          height: 1.4,
        ),
        labelSmall: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          color: AbyssalColors.textMuted,
          letterSpacing: 0.8,
        ),
      ),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: AbyssalColors.surface,
      foregroundColor: AbyssalColors.textPrimary,
      elevation: 0,
      scrolledUnderElevation: 0,
    ),
    cardTheme: CardTheme(
      color: AbyssalColors.card,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: const BorderSide(color: AbyssalColors.borderSubtle, width: 1),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: AbyssalColors.surface,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AbyssalColors.borderSubtle),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AbyssalColors.borderSubtle),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(14),
        borderSide: const BorderSide(color: AbyssalColors.cyan, width: 1.5),
      ),
      hintStyle: const TextStyle(color: AbyssalColors.textMuted),
      contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: AbyssalColors.cyan,
        foregroundColor: AbyssalColors.background,
        elevation: 0,
        padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        textStyle: const TextStyle(
          fontSize: 14,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.3,
        ),
      ),
    ),
    iconTheme: const IconThemeData(color: AbyssalColors.textSecondary, size: 20),
    dividerTheme: const DividerThemeData(
      color: AbyssalColors.borderSubtle,
      thickness: 1,
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AbyssalColors.cyan,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: AbyssalColors.card,
      contentTextStyle: const TextStyle(color: AbyssalColors.textPrimary),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      behavior: SnackBarBehavior.floating,
    ),
  );
}
