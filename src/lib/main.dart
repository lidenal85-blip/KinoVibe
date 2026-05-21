import 'package:flutter/material.dart';
import 'theme/abyssal_theme.dart';
import 'screens/hub_screen.dart';

void main() {
  runApp(const KinoVibeApp());
}

class KinoVibeApp extends StatelessWidget {
  const KinoVibeApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'KinoVibe v5.0',
      debugShowCheckedModeBanner: false,
      theme: AbyssalTheme.theme,
      home: const HubScreen(),
    );
  }
}
