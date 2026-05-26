// ignore: avoid_web_libraries_in_flutter
import 'dart:convert';
// ignore: avoid_web_libraries_in_flutter
import 'dart:html' as html;
// ignore: avoid_web_libraries_in_flutter
import 'dart:js' as js;
import 'dart:math';
// ignore: avoid_web_libraries_in_flutter
import 'dart:ui_web' as ui_web;
import 'package:flutter/material.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/movie.dart';
import '../services/api_service.dart';
import '../theme/abyssal_theme.dart';

class WatchScreen extends StatefulWidget {
  final Movie movie;
  final String? roomId;
  final bool isJoining;
  final bool createParty;

  const WatchScreen({
    super.key,
    required this.movie,
    this.roomId,
    this.isJoining = false,
    this.createParty = false,
  });

  @override
  State<WatchScreen> createState() => _WatchScreenState();
}

class _WatchScreenState extends State<WatchScreen> {
  final _api = ApiService();

  late Movie _movie;
  Map<String, dynamic>? _streamInfo;
  bool _streamLoading = true;
  String? _streamError;

  // Watch Party / WS
  WebSocketChannel? _ws;
  final String _peerId = _makePeerId();
  bool _wsConnected = false;
  int _peersCount = 0;
  String? _activeRoomId;
  String? _inviteUrl;
  bool _creatingParty = false;
  final _messages = <_ChatMsg>[];

  html.VideoElement? _videoEl;

  // YouTube HLS state
  String? _ytHlsStreamId;
  String _ytHlsStatus = 'idle';
  int _ytHlsSegments = 0;
  String? _ytHlsError;
  bool _ytHlsPolling = false;

  static bool _isYoutubeUrl(String url) =>
      url.contains('youtube.com') || url.contains('youtu.be');

  static String _makePeerId() {
    const c = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    final r = Random.secure();
    return List.generate(8, (_) => c[r.nextInt(c.length)]).join();
  }

  @override
  void initState() {
    super.initState();
    _movie = widget.movie;
    if (!widget.isJoining) _loadStream();
    if (widget.roomId != null) _connectToRoom(widget.roomId!);
    if (widget.createParty) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _startWatchParty());
    }
  }

  Future<void> _loadStream({String? url}) async {
    final target = url ?? _movie.url;
    if (target.isEmpty) return;

    // Magnet links → WebTorrent player, no backend call needed
    if (_movie.isMagnet || target.startsWith('magnet:')) {
      setState(() { _streamLoading = false; _streamError = null; });
      return;
    }

    // Site-only results (kinozal without magnet) → direct-open UI, no backend call
    if (_movie.isSiteOnly) {
      setState(() { _streamLoading = false; _streamError = null; });
      return;
    }

    // YouTube → HLS pipeline (bypass direct stream extraction)
    if (_movie.provider == 'youtube' || _isYoutubeUrl(target)) {
      setState(() { _streamLoading = false; _streamError = null; });
      _startYoutubeHls(target);
      return;
    }

    setState(() { _streamLoading = true; _streamError = null; });
    try {
      final info = await _api.getStream(target, provider: _movie.provider);
      if (mounted) setState(() { _streamInfo = info; _streamLoading = false; });
    } catch (e) {
      if (mounted) setState(() {
        _streamError = e.toString().replaceAll('Exception: Stream failed: ', '');
        _streamLoading = false;
      });
    }
  }

  // ─── Watch Party ────────────────────────────────────────────────────────────

  Future<void> _startWatchParty() async {
    if (_creatingParty) return;
    setState(() => _creatingParty = true);
    try {
      final data = await _api.createRoom(
        movieUrl: _movie.url,
        movieTitle: _movie.title,
      );
      final roomId = data['room_id'] as String? ?? '';
      final inviteUrl = data['invite_url'] as String? ?? '';
      if (!mounted) return;
      setState(() {
        _inviteUrl = inviteUrl;
        _creatingParty = false;
      });
      _connectToRoom(roomId);
    } catch (e) {
      if (!mounted) return;
      setState(() => _creatingParty = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Ошибка создания комнаты: $e',
              style: const TextStyle(color: AbyssalColors.textPrimary)),
          backgroundColor: AbyssalColors.card,
        ),
      );
    }
  }

  void _copyInviteLink() {
    final link = _inviteUrl ?? _activeRoomId ?? '';
    if (link.isEmpty) return;
    _copyToClipboard(link);
  }

  void _copyToClipboard(String text) {
    // Primary: use JS clipboard API directly (works on both HTTP and HTTPS)
    try {
      js.context.callMethod('eval', [
        'navigator.clipboard.writeText(${json.encode(text)}).catch(e=>console.error(e))'
      ]);
      _showCopySnack(text);
      return;
    } catch (_) {
      // Fall through to dart:html Clipboard API
    }
    final clipboard = html.window.navigator.clipboard;
    if (clipboard != null) {
      clipboard.writeText(text).then((_) => _showCopySnack(text)).catchError((_) => _execCommandCopy(text));
    } else {
      _execCommandCopy(text);
    }
  }

  void _execCommandCopy(String text) {
    // Fallback for HTTP (non-HTTPS) where Clipboard API is unavailable
    final ta = html.TextAreaElement()
      ..value = text
      ..style.position = 'fixed'
      ..style.left = '-9999px'
      ..style.top = '0';
    html.document.body?.append(ta);
    ta.select();
    html.document.execCommand('copy');
    ta.remove();
    _showCopySnack(text);
  }

  void _showCopySnack(String link) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          'Ссылка скопирована: $link',
          style: const TextStyle(color: AbyssalColors.textPrimary),
        ),
        backgroundColor: AbyssalColors.card,
        duration: const Duration(seconds: 3),
      ),
    );
  }

  // ─── WebSocket ──────────────────────────────────────────────────────────────

  void _connectToRoom(String roomId) {
    setState(() => _activeRoomId = roomId);
    final proto = html.window.location.protocol == 'https:' ? 'wss' : 'ws';
    final host = html.window.location.host;
    _ws = WebSocketChannel.connect(Uri.parse('$proto://$host/api/ws/$_peerId'));
    _ws!.sink.add(jsonEncode({'type': 'join_room', 'room_id': roomId}));
    _ws!.stream.listen(
      _onWs,
      onDone: () { if (mounted) setState(() => _wsConnected = false); },
      onError: (_) { if (mounted) setState(() => _wsConnected = false); },
    );
  }

  void _onWs(dynamic raw) {
    if (!mounted) return;
    final msg = jsonDecode(raw as String) as Map<String, dynamic>;
    final t = msg['type'] as String?;
    setState(() {
      switch (t) {
        case 'room_joined':
          _wsConnected = true;
          _peersCount = (msg['peers_count'] as num?)?.toInt() ?? 1;
          _activeRoomId = msg['room_id'] as String?;
          if (widget.isJoining) {
            final movieUrl = msg['movie_url'] as String? ?? '';
            final movieTitle = msg['movie_title'] as String? ?? 'Watch Party';
            _movie = Movie(title: movieTitle, url: movieUrl, provider: 'unknown');
            if (movieUrl.isNotEmpty) _loadStream(url: movieUrl);
          }
        case 'peer_joined' || 'peer_left':
          _peersCount = (msg['peers_count'] as num?)?.toInt() ?? _peersCount;
        case 'chat':
          final from = msg['from_peer'] as String? ?? '??';
          _messages.add(_ChatMsg(
            text: msg['text'] as String? ?? '',
            isMe: false,
            sender: from.substring(0, min(6, from.length)),
          ));
        case 'sync':
          _applySync(msg);
      }
    });
  }

  void _applySync(Map<String, dynamic> msg) {
    final action = msg['action'] as String?;
    final pos = (msg['position_sec'] as num?)?.toDouble() ?? 0.0;
    if (_videoEl != null) {
      _videoEl!.currentTime = pos;
      if (action == 'play') { _videoEl!.play(); }
      else if (action == 'pause') { _videoEl!.pause(); }
    }
  }

  void _sendChat(String text) {
    if (text.isEmpty) return;
    _ws?.sink.add(jsonEncode({'type': 'chat', 'text': text}));
    setState(() => _messages.add(_ChatMsg(text: text, isMe: true)));
  }

  void _sendSync(String action, double pos) {
    _ws?.sink.add(jsonEncode({
      'type': 'sync',
      'action': action,
      'position_sec': pos,
    }));
  }

  @override
  void dispose() {
    _ws?.sink.close();
    super.dispose();
  }

  Future<void> _startYoutubeHls(String url) async {
    setState(() {
      _ytHlsStreamId = null;
      _ytHlsStatus = 'processing';
      _ytHlsSegments = 0;
      _ytHlsError = null;
    });
    try {
      final data = await _api.hlsStart(url);
      final sid = data['stream_id'] as String? ?? '';
      if (!mounted || sid.isEmpty) return;
      setState(() => _ytHlsStreamId = sid);
      _pollYoutubeHls(sid);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _ytHlsStatus = 'error';
        _ytHlsError = e.toString();
      });
    }
  }

  void _pollYoutubeHls(String streamId) {
    if (_ytHlsPolling) return;
    _ytHlsPolling = true;
    Future.doWhile(() async {
      await Future.delayed(const Duration(seconds: 3));
      if (!mounted) { _ytHlsPolling = false; return false; }
      try {
        final data = await _api.hlsStatus(streamId);
        final status = data['status'] as String? ?? 'processing';
        final segments = (data['segments'] as num?)?.toInt() ?? 0;
        if (!mounted) { _ytHlsPolling = false; return false; }
        setState(() {
          _ytHlsStatus = status;
          _ytHlsSegments = segments;
          if (status == 'error') _ytHlsError = data['error'] as String? ?? 'Ошибка потока';
          if (status == 'ready' || segments >= 2) _ytHlsStatus = 'ready';
        });
        if (_ytHlsStatus == 'error' || _ytHlsStatus == 'ready') {
          _ytHlsPolling = false;
          return false;
        }
        return true;
      } catch (_) {
        return true;
      }
    });
  }

  void _openOnSite() {
    final url = _movie.url;
    if (url.isEmpty || url.startsWith('magnet:')) return;
    html.window.open(url, '_blank');
  }

  bool get _canOpenOnSite {
    final url = _movie.url;
    return url.isNotEmpty && !url.startsWith('magnet:');
  }

  // ─── Build ──────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    final isWide = MediaQuery.of(context).size.width > 900;
    final roomId = _activeRoomId ?? widget.roomId;

    return Scaffold(
      backgroundColor: AbyssalColors.background,
      appBar: AppBar(
        backgroundColor: AbyssalColors.surface,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_rounded, color: AbyssalColors.cyan),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: Text(
          _movie.title,
          style: const TextStyle(
              color: AbyssalColors.textPrimary,
              fontSize: 16,
              fontWeight: FontWeight.w700),
          overflow: TextOverflow.ellipsis,
        ),
        actions: [
          // Open on site button — always visible for non-magnet URLs
          if (_canOpenOnSite)
            Padding(
              padding: const EdgeInsets.only(right: 4),
              child: Tooltip(
                message: 'Открыть на сайте (${_movie.provider.toUpperCase()})',
                child: IconButton(
                  icon: const Icon(Icons.open_in_new_rounded, size: 20),
                  color: AbyssalColors.textSecondary,
                  onPressed: _openOnSite,
                ),
              ),
            ),
          if (roomId != null)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: _PartyBadge(
                roomId: roomId,
                peers: _peersCount,
                connected: _wsConnected,
              ),
            ),
          const SizedBox(width: 8),
        ],
        bottom: const PreferredSize(
          preferredSize: Size.fromHeight(1),
          child: Divider(height: 1, color: AbyssalColors.borderSubtle),
        ),
      ),
      body: Column(
        children: [
          _buildPlayerArea(),
          // Watch Party controls
          _buildPartyControls(),
          if (isWide)
            Expanded(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(flex: 2, child: _MovieInfo(movie: _movie)),
                  const VerticalDivider(width: 1, color: AbyssalColors.borderSubtle),
                  Expanded(
                    child: _ChatPanel(
                      messages: _messages,
                      onSend: _sendChat,
                      isConnected: _wsConnected,
                      peersCount: _peersCount,
                      roomId: roomId,
                    ),
                  ),
                ],
              ),
            )
          else
            Expanded(
              child: DefaultTabController(
                length: 2,
                child: Column(
                  children: [
                    const TabBar(
                      indicatorColor: AbyssalColors.cyan,
                      labelColor: AbyssalColors.cyan,
                      unselectedLabelColor: AbyssalColors.textMuted,
                      tabs: [Tab(text: 'Инфо'), Tab(text: 'Чат')],
                    ),
                    Expanded(
                      child: TabBarView(
                        children: [
                          _MovieInfo(movie: _movie),
                          _ChatPanel(
                            messages: _messages,
                            onSend: _sendChat,
                            isConnected: _wsConnected,
                            peersCount: _peersCount,
                            roomId: roomId,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
  }

  // ─── Watch Party Panel ────────────────────────────────────────────────────

  Widget _buildPartyControls() {
    if (_activeRoomId != null) {
      // Room is active — show invite link copy button
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        decoration: const BoxDecoration(
          color: AbyssalColors.surface,
          border: Border(bottom: BorderSide(color: AbyssalColors.borderSubtle)),
        ),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: const BoxDecoration(
                color: AbyssalColors.success,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                _inviteUrl ?? 'Комната: ${_activeRoomId ?? ''}',
                style: const TextStyle(
                    color: AbyssalColors.textSecondary, fontSize: 12),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: 8),
            GestureDetector(
              onTap: _copyInviteLink,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                decoration: BoxDecoration(
                  color: AbyssalColors.violet.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: AbyssalColors.violet.withOpacity(0.5)),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.copy_rounded, size: 14, color: AbyssalColors.violetGlow),
                    SizedBox(width: 6),
                    Text(
                      'Скопировать ссылку',
                      style: TextStyle(
                          color: AbyssalColors.violetGlow,
                          fontSize: 12,
                          fontWeight: FontWeight.w700),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      );
    }

    // No room yet — show "Watch Party" button
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: const BoxDecoration(
        color: AbyssalColors.surface,
        border: Border(bottom: BorderSide(color: AbyssalColors.borderSubtle)),
      ),
      child: Row(
        children: [
          const Icon(Icons.groups_rounded, size: 18, color: AbyssalColors.textMuted),
          const SizedBox(width: 10),
          const Expanded(
            child: Text(
              'Смотри вместе с друзьями',
              style: TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
            ),
          ),
          GestureDetector(
            onTap: _creatingParty ? null : _startWatchParty,
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              decoration: BoxDecoration(
                color: AbyssalColors.violet.withOpacity(_creatingParty ? 0.08 : 0.18),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(
                  color: AbyssalColors.violet.withOpacity(_creatingParty ? 0.3 : 0.6),
                ),
              ),
              child: _creatingParty
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                          color: AbyssalColors.violet, strokeWidth: 2),
                    )
                  : const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.party_mode_rounded,
                            size: 16, color: AbyssalColors.violetGlow),
                        SizedBox(width: 7),
                        Text(
                          'Watch Party',
                          style: TextStyle(
                              color: AbyssalColors.violetGlow,
                              fontSize: 13,
                              fontWeight: FontWeight.w700),
                        ),
                      ],
                    ),
            ),
          ),
        ],
      ),
    );
  }

  // ─── Player ─────────────────────────────────────────────────────────────────

  Widget _buildPlayerArea() {
    final isWide = MediaQuery.of(context).size.width > 900;
    final h = isWide
        ? MediaQuery.of(context).size.height * 0.5
        : MediaQuery.of(context).size.width * 9 / 16;

    // ── Magnet → WebTorrent iframe ──────────────────────────────────────────
    if (_movie.isMagnet || _movie.url.startsWith('magnet:')) {
      return _WebTorrentPlayer(magnetUrl: _movie.url, height: h);
    }

    // ── Site-only (kinozal without magnet) → open-on-site UI ───────────────
    if (_movie.isSiteOnly) {
      return _PlayerShell(
        height: h,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: BoxDecoration(
                  color: AbyssalColors.surface,
                  shape: BoxShape.circle,
                  border: Border.all(color: AbyssalColors.borderSubtle),
                ),
                child: const Icon(Icons.download_rounded,
                    size: 48, color: AbyssalColors.cyan),
              ),
              const SizedBox(height: 20),
              const Text('Торрент-раздача',
                  style: TextStyle(
                      color: AbyssalColors.textPrimary,
                      fontSize: 18,
                      fontWeight: FontWeight.w700)),
              const SizedBox(height: 8),
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 40),
                child: Text('Прямая magnet-ссылка не найдена.\nОткройте страницу раздачи чтобы скачать.',
                    style: TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
                    textAlign: TextAlign.center),
              ),
              const SizedBox(height: 20),
              GestureDetector(
                onTap: _openOnSite,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                  decoration: BoxDecoration(
                    color: AbyssalColors.cyan.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AbyssalColors.cyan.withOpacity(0.5)),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.open_in_new_rounded, size: 18, color: AbyssalColors.cyan),
                      SizedBox(width: 8),
                      Text('Открыть на Kinozal',
                          style: TextStyle(color: AbyssalColors.cyan,
                              fontWeight: FontWeight.w700, fontSize: 14)),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      );
    }

    // YouTube → HLS player
    if (_movie.provider == 'youtube' || _isYoutubeUrl(_movie.url)) {
      if (_ytHlsStatus == 'error') {
        return _PlayerShell(
          height: h,
          child: Center(child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.error_outline_rounded, color: Colors.red, size: 48),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 32),
                child: Text(
                  _ytHlsError ?? 'Ошибка HLS потока',
                  style: const TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
                  textAlign: TextAlign.center,
                ),
              ),
            ],
          )),
        );
      }
      if (_ytHlsStatus == 'ready' && _ytHlsStreamId != null) {
        return _YtHlsPlayer(streamId: _ytHlsStreamId!, height: h);
      }
      return _PlayerShell(
        height: h,
        child: Center(child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const CircularProgressIndicator(color: AbyssalColors.cyan),
            const SizedBox(height: 16),
            Text(
              'Подготовка потока... $_ytHlsSegments сегментов',
              style: const TextStyle(color: AbyssalColors.textMuted, fontSize: 13),
            ),
          ],
        )),
      );
    }

    if (_streamLoading) {
      return _PlayerShell(
        height: h,
        child: const Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              CircularProgressIndicator(color: AbyssalColors.cyan),
              SizedBox(height: 16),
              Text('Извлекаем видеопоток…',
                  style: TextStyle(color: AbyssalColors.textMuted)),
            ],
          ),
        ),
      );
    }

    if (_streamError != null) {
      return _PlayerShell(
        height: h,
        child: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.play_circle_outline_rounded,
                  color: AbyssalColors.textMuted, size: 56),
              const SizedBox(height: 16),
              const Text('Встроенный плеер недоступен',
                  style: TextStyle(
                      color: AbyssalColors.textPrimary,
                      fontSize: 16,
                      fontWeight: FontWeight.w600)),
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 40),
                child: Text(
                  _streamError!.length > 120
                      ? '${_streamError!.substring(0, 120)}…'
                      : _streamError!,
                  style: const TextStyle(
                      color: AbyssalColors.textMuted, fontSize: 11),
                  textAlign: TextAlign.center,
                ),
              ),
              if (_canOpenOnSite) ...[
                const SizedBox(height: 20),
                GestureDetector(
                  onTap: _openOnSite,
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                    decoration: BoxDecoration(
                      color: AbyssalColors.cyan.withOpacity(0.12),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: AbyssalColors.cyan.withOpacity(0.5)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.open_in_new_rounded,
                            size: 18, color: AbyssalColors.cyan),
                        const SizedBox(width: 8),
                        Text(
                          'Открыть на ${_movie.provider.toUpperCase()}',
                          style: const TextStyle(
                              color: AbyssalColors.cyan,
                              fontWeight: FontWeight.w700,
                              fontSize: 14),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      );
    }

    final info = _streamInfo;
    if (info == null) {
      return _PlayerShell(
        height: h,
        child: const Center(
          child: Text('Поток недоступен',
              style: TextStyle(color: AbyssalColors.textMuted)),
        ),
      );
    }

    final embedUrl = info['embed_url'] as String?;
    final streamUrl = info['stream_url'] as String? ?? info['url'] as String?;

    if ((embedUrl == null || embedUrl.isEmpty) &&
        (streamUrl == null || streamUrl.isEmpty)) {
      return _PlayerShell(
        height: h,
        child: const Center(
          child: Text('Поток недоступен',
              style: TextStyle(color: AbyssalColors.textMuted)),
        ),
      );
    }

    return _WebVideoPlayer(
      embedUrl: embedUrl,
      streamUrl: streamUrl,
      height: h,
      onVideoReady: (el) => setState(() => _videoEl = el),
      onSync: _activeRoomId != null ? _sendSync : null,
    );
  }
}

// ─── _WebTorrentPlayer ───────────────────────────────────────────────────────

class _WebTorrentPlayer extends StatefulWidget {
  final String magnetUrl;
  final double height;
  const _WebTorrentPlayer({required this.magnetUrl, required this.height});

  @override
  State<_WebTorrentPlayer> createState() => _WebTorrentPlayerState();
}

class _WebTorrentPlayerState extends State<_WebTorrentPlayer> {
  late final String _viewId;

  @override
  void initState() {
    super.initState();
    _viewId = 'wt-${DateTime.now().microsecondsSinceEpoch}';
    _register();
  }

  void _register() {
    final encodedMagnet = Uri.encodeComponent(widget.magnetUrl);
    ui_web.platformViewRegistry.registerViewFactory(_viewId, (int id) {
      final iframe = html.IFrameElement()
        ..src = '/torrent_player.html?magnet=$encodedMagnet'
        ..style.border = 'none'
        ..style.width = '100%'
        ..style.height = '100%'
        ..allowFullscreen = true;
      iframe.setAttribute('allow', 'autoplay; fullscreen; encrypted-media');
      return iframe;
    });
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    width: double.infinity,
    height: widget.height,
    child: HtmlElementView(viewType: _viewId),
  );
}

// ─── _PlayerShell ─────────────────────────────────────────────────────────────

class _PlayerShell extends StatelessWidget {
  final double height;
  final Widget child;

  const _PlayerShell({required this.height, required this.child});

  @override
  Widget build(BuildContext context) => Container(
      width: double.infinity,
      height: height,
      color: Colors.black,
      child: child);
}

// ─── _WebVideoPlayer ──────────────────────────────────────────────────────────

class _WebVideoPlayer extends StatefulWidget {
  final String? embedUrl;
  final String? streamUrl;
  final double height;
  final void Function(html.VideoElement)? onVideoReady;
  final void Function(String action, double pos)? onSync;

  const _WebVideoPlayer({
    this.embedUrl,
    this.streamUrl,
    required this.height,
    this.onVideoReady,
    this.onSync,
  });

  @override
  State<_WebVideoPlayer> createState() => _WebVideoPlayerState();
}

class _WebVideoPlayerState extends State<_WebVideoPlayer> {
  late final String _viewId;

  @override
  void initState() {
    super.initState();
    _viewId = 'kv-${DateTime.now().microsecondsSinceEpoch}';
    _register();
  }

  void _register() {
    final embedUrl = widget.embedUrl;
    final streamUrl = widget.streamUrl;
    final onSync = widget.onSync;
    final onVideoReady = widget.onVideoReady;

    ui_web.platformViewRegistry.registerViewFactory(_viewId, (int id) {
      if (embedUrl != null && embedUrl.isNotEmpty) {
        final iframe = html.IFrameElement()
          ..src = embedUrl
          ..style.border = 'none'
          ..style.width = '100%'
          ..style.height = '100%'
          ..allowFullscreen = true;
        iframe.setAttribute(
            'allow', 'autoplay; fullscreen; encrypted-media; picture-in-picture');
        return iframe;
      }

      final video = html.VideoElement()
        ..src = streamUrl ?? ''
        ..controls = true
        ..style.width = '100%'
        ..style.height = '100%'
        ..style.background = '#000000';

      if (onSync != null) {
        video.onPlay.listen((_) => onSync('play', video.currentTime.toDouble()));
        video.onPause.listen((_) => onSync('pause', video.currentTime.toDouble()));
        video.onSeeked.listen((_) => onSync('seek', video.currentTime.toDouble()));
      }

      Future.microtask(() => onVideoReady?.call(video));
      return video;
    });
  }

  @override
  Widget build(BuildContext context) => SizedBox(
      width: double.infinity,
      height: widget.height,
      child: HtmlElementView(viewType: _viewId));
}

// ─── _YtHlsPlayer ─────────────────────────────────────────────────────────────

class _YtHlsPlayer extends StatefulWidget {
  final String streamId;
  final double height;
  const _YtHlsPlayer({required this.streamId, required this.height});

  @override
  State<_YtHlsPlayer> createState() => _YtHlsPlayerState();
}

class _YtHlsPlayerState extends State<_YtHlsPlayer> {
  late final String _viewId;

  @override
  void initState() {
    super.initState();
    _viewId = 'yt-hls-${DateTime.now().microsecondsSinceEpoch}';
    _register();
  }

  void _register() {
    final hlsPath = '/hls/${widget.streamId}/stream.m3u8';
    final videoId = 'yt-hls-vid-${widget.streamId}';

    ui_web.platformViewRegistry.registerViewFactory(_viewId, (int id) {
      final video = html.VideoElement()
        ..id = videoId
        ..controls = true
        ..autoplay = true
        ..style.width = '100%'
        ..style.height = '100%'
        ..style.background = '#000';

      Future.delayed(const Duration(milliseconds: 300), () {
        js.context.callMethod('eval', ['''
          (function() {
            var v = document.getElementById('$videoId');
            if (!v) return;
            if (window.Hls && Hls.isSupported()) {
              var h = new Hls({debug: false});
              h.loadSource('$hlsPath');
              h.attachMedia(v);
              h.on(Hls.Events.MANIFEST_PARSED, function() {
                v.play().catch(function() {});
              });
            } else if (v.canPlayType('application/vnd.apple.mpegurl')) {
              v.src = '$hlsPath';
              v.play().catch(function() {});
            }
          })();
        ''']);
      });

      return video;
    });
  }

  @override
  Widget build(BuildContext context) => SizedBox(
    width: double.infinity,
    height: widget.height,
    child: HtmlElementView(viewType: _viewId),
  );
}

// ─── _PartyBadge ──────────────────────────────────────────────────────────────

class _PartyBadge extends StatelessWidget {
  final String roomId;
  final int peers;
  final bool connected;

  const _PartyBadge({
    required this.roomId,
    required this.peers,
    required this.connected,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: AbyssalColors.violet.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: connected
              ? AbyssalColors.violet.withOpacity(0.5)
              : AbyssalColors.borderSubtle,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(
              color: connected ? AbyssalColors.success : AbyssalColors.textMuted,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 6),
          Text(
            '$roomId · $peers',
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w700,
              color: connected ? AbyssalColors.violet : AbyssalColors.textMuted,
              letterSpacing: 0.5,
            ),
          ),
        ],
      ),
    );
  }
}

// ─── _MovieInfo ───────────────────────────────────────────────────────────────

class _MovieInfo extends StatelessWidget {
  final Movie movie;
  const _MovieInfo({required this.movie});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(movie.title, style: Theme.of(context).textTheme.displayMedium),
          if (movie.year != null || movie.rating != null) ...[
            const SizedBox(height: 8),
            Row(children: [
              if (movie.year != null) ...[
                const Icon(Icons.calendar_today_rounded,
                    size: 14, color: AbyssalColors.textMuted),
                const SizedBox(width: 4),
                Text(movie.year!, style: Theme.of(context).textTheme.bodyMedium),
                const SizedBox(width: 16),
              ],
              if (movie.rating != null) ...[
                const Icon(Icons.star_rounded, size: 14, color: AbyssalColors.warning),
                const SizedBox(width: 4),
                Text(movie.rating!, style: Theme.of(context).textTheme.bodyMedium),
              ],
            ]),
          ],
          if (movie.description != null) ...[
            const SizedBox(height: 16),
            Text(movie.description!, style: Theme.of(context).textTheme.bodyLarge),
          ],
        ],
      ),
    );
  }
}

// ─── _ChatPanel ───────────────────────────────────────────────────────────────

class _ChatMsg {
  final String text;
  final bool isMe;
  final String? sender;
  const _ChatMsg({required this.text, required this.isMe, this.sender});
}

class _ChatPanel extends StatefulWidget {
  final List<_ChatMsg> messages;
  final void Function(String) onSend;
  final bool isConnected;
  final int peersCount;
  final String? roomId;

  const _ChatPanel({
    required this.messages,
    required this.onSend,
    this.isConnected = false,
    this.peersCount = 0,
    this.roomId,
  });

  @override
  State<_ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends State<_ChatPanel> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();

  @override
  void didUpdateWidget(_ChatPanel old) {
    super.didUpdateWidget(old);
    if (widget.messages.length > old.messages.length) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) {
          _scroll.animateTo(
            _scroll.position.maxScrollExtent,
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
          );
        }
      });
    }
  }

  void _send() {
    final t = _ctrl.text.trim();
    if (t.isEmpty) return;
    widget.onSend(t);
    _ctrl.clear();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final canSend = widget.roomId == null || widget.isConnected;

    return Column(
      children: [
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: const BoxDecoration(
            border: Border(bottom: BorderSide(color: AbyssalColors.borderSubtle)),
          ),
          child: Row(
            children: [
              const Icon(Icons.chat_bubble_outline_rounded,
                  size: 16, color: AbyssalColors.violet),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  widget.roomId != null ? 'Чат · ${widget.roomId}' : 'Чат вечеринки',
                  style: const TextStyle(
                      color: AbyssalColors.textPrimary,
                      fontSize: 14,
                      fontWeight: FontWeight.w600),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (widget.peersCount > 0) ...[
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                  decoration: BoxDecoration(
                    color: AbyssalColors.violet.withOpacity(0.12),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: AbyssalColors.violet.withOpacity(0.4)),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.group_rounded, size: 11, color: AbyssalColors.violet),
                      const SizedBox(width: 4),
                      Text('${widget.peersCount}',
                          style: const TextStyle(
                              color: AbyssalColors.violet,
                              fontSize: 11,
                              fontWeight: FontWeight.w700)),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
        Expanded(
          child: widget.messages.isEmpty
              ? Center(
                  child: widget.roomId == null
                      ? const Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.lock_outline_rounded,
                                color: AbyssalColors.textMuted, size: 28),
                            SizedBox(height: 10),
                            Text('Создай Watch Party\nчтобы общаться в чате',
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                    color: AbyssalColors.textMuted, fontSize: 12)),
                          ],
                        )
                      : !widget.isConnected
                          ? const Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                SizedBox(
                                  width: 20,
                                  height: 20,
                                  child: CircularProgressIndicator(
                                      color: AbyssalColors.violet, strokeWidth: 2),
                                ),
                                SizedBox(height: 10),
                                Text('Подключение к комнате...',
                                    style: TextStyle(
                                        color: AbyssalColors.textMuted, fontSize: 12)),
                              ],
                            )
                          : const Text('Пока тихо... Начни общение!',
                              style: TextStyle(
                                  color: AbyssalColors.textMuted, fontSize: 13)),
                )
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(12),
                  itemCount: widget.messages.length,
                  itemBuilder: (_, i) => _MessageBubble(msg: widget.messages[i]),
                ),
        ),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: const BoxDecoration(
            border: Border(top: BorderSide(color: AbyssalColors.borderSubtle)),
          ),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _ctrl,
                  onSubmitted: (_) => canSend ? _send() : null,
                  enabled: canSend,
                  style: const TextStyle(color: AbyssalColors.textPrimary, fontSize: 13),
                  decoration: InputDecoration(
                    hintText: widget.roomId != null && !widget.isConnected
                        ? 'Подключение...'
                        : 'Написать...',
                    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              GestureDetector(
                onTap: canSend ? _send : null,
                child: Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(
                    color: canSend
                        ? AbyssalColors.cyan.withOpacity(0.15)
                        : AbyssalColors.surface,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(
                      color: canSend
                          ? AbyssalColors.cyan.withOpacity(0.4)
                          : AbyssalColors.borderSubtle,
                    ),
                  ),
                  child: Icon(Icons.send_rounded,
                      size: 18,
                      color: canSend ? AbyssalColors.cyan : AbyssalColors.textMuted),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MessageBubble extends StatelessWidget {
  final _ChatMsg msg;
  const _MessageBubble({required this.msg});

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: msg.isMe ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.7),
        decoration: BoxDecoration(
          color: msg.isMe
              ? AbyssalColors.cyan.withOpacity(0.15)
              : AbyssalColors.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: msg.isMe
                ? AbyssalColors.cyan.withOpacity(0.3)
                : AbyssalColors.borderSubtle,
          ),
        ),
        child: Column(
          crossAxisAlignment:
              msg.isMe ? CrossAxisAlignment.end : CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (!msg.isMe && msg.sender != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(msg.sender!,
                    style: const TextStyle(
                        color: AbyssalColors.violet,
                        fontSize: 10,
                        fontWeight: FontWeight.w700)),
              ),
            Text(msg.text,
                style: const TextStyle(
                    color: AbyssalColors.textPrimary, fontSize: 13)),
          ],
        ),
      ),
    );
  }
}
