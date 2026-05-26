"""
signaling.py — WebSocket сигнальный сервер для WebRTC
Версия 2.0: роли (host/admin/guest), демократия, таймлайн, оффлайн-реконнект
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("kinovibe.signaling")

OFFLINE_GRACE_SECONDS = 180
VOTE_TIMEOUT_SECONDS = 15
MAX_PEERS_PER_ROOM = 8


@dataclass
class Peer:
    peer_id: str
    ws: WebSocket
    room_id: str = ""
    nick: str = ""
    role: str = "guest"           # host | admin | guest
    status: str = "connected"     # connected | offline
    offline_since: float = 0.0


@dataclass
class Room:
    room_id: str
    movie_url: str = ""
    movie_title: str = ""
    host_id: str = ""
    admin_ids: set = field(default_factory=set)
    peers: dict = field(default_factory=dict)  # peer_id → Peer

    # Player state
    is_playing: bool = False
    paused_at: float = 0.0       # position_sec at last pause/seek
    timeline_start: float = 0.0  # time.time() when play started from paused_at

    # Democracy
    democracy_mode: bool = False
    active_vote: dict = field(default_factory=dict)
    vote_task: object = None     # asyncio.Task (not typed to avoid import issue)

    # Lobby / state
    state: str = "playing"       # lobby | playing
    lobby_trailers: list = field(default_factory=list)
    lobby_index: int = 0

    def is_full(self) -> bool:
        connected = [p for p in self.peers.values() if p.status == "connected"]
        return len(connected) >= MAX_PEERS_PER_ROOM

    def connected_peers(self) -> list[Peer]:
        return [p for p in self.peers.values() if p.status == "connected"]

    def other_connected(self, exclude_id: str) -> list[Peer]:
        return [p for p in self.peers.values()
                if p.status == "connected" and p.peer_id != exclude_id]

    def get_position(self) -> float:
        """Calculate current playback position (server-authoritative)."""
        if self.is_playing and self.timeline_start:
            return self.paused_at + (time.time() - self.timeline_start)
        return self.paused_at


class SignalingManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._peers: dict[str, Peer] = {}

    # ── Public API ───────────────────────────────────────────────────────────────

    def create_room(self, movie_url: str = "", movie_title: str = "") -> str:
        room_id = str(uuid.uuid4())[:8].upper()
        self._rooms[room_id] = Room(
            room_id=room_id,
            movie_url=movie_url,
            movie_title=movie_title,
        )
        logger.info(f"[SIGNALING] Room created: {room_id} | {movie_title[:40]}")
        return room_id

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def room_list(self) -> list[dict]:
        return [
            {
                "room_id": r.room_id,
                "movie_title": r.movie_title,
                "peers": len(r.connected_peers()),
            }
            for r in self._rooms.values()
        ]

    # ── WebSocket handler ────────────────────────────────────────────────────────

    async def handle(self, ws: WebSocket, peer_id: str):
        await ws.accept()
        peer = Peer(peer_id=peer_id, ws=ws)
        self._peers[peer_id] = peer
        logger.info(f"[SIGNALING] Peer connected: {peer_id}")

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await self._send(ws, {"type": "error", "msg": "invalid_json"})
                    continue
                await self._dispatch(peer, msg)
        except WebSocketDisconnect:
            await self._on_disconnect(peer)

    # ── Message dispatch ─────────────────────────────────────────────────────────

    async def _dispatch(self, peer: Peer, msg: dict):
        t = msg.get("type")
        room = self._get_peer_room(peer)

        # ── Room management ──────────────────────────────────────────────────────
        if t == "create_room":
            room_id = self.create_room(
                movie_url=msg.get("movie_url", ""),
                movie_title=msg.get("movie_title", ""),
            )
            peer.room_id = room_id
            peer.role = "host"
            peer.nick = msg.get("nick", peer.peer_id[:6])
            room = self._rooms[room_id]
            room.host_id = peer.peer_id
            room.peers[peer.peer_id] = peer
            await self._send(peer.ws, {
                "type": "room_created",
                "room_id": room_id,
                "peer_id": peer.peer_id,
                "role": "host",
            })

        elif t == "join_room":
            room_id = msg.get("room_id", "").upper()
            room = self._rooms.get(room_id)

            # Rejoin: offline peer reconnecting with same peer_id
            if room and peer.peer_id in room.peers:
                existing = room.peers[peer.peer_id]
                if existing.status == "offline":
                    existing.ws = peer.ws
                    existing.status = "connected"
                    existing.offline_since = 0.0
                    self._peers[peer.peer_id] = existing
                    peer = existing
                    await self._send(peer.ws, {
                        "type": "room_rejoined",
                        "room_id": room_id,
                        "peer_id": peer.peer_id,
                        "role": peer.role,
                        "movie_url": room.movie_url,
                        "movie_title": room.movie_title,
                        "is_playing": room.is_playing,
                        "position_sec": room.get_position(),
                        "peers_count": len(room.connected_peers()),
                        "democracy_mode": room.democracy_mode,
                        "server_timestamp": time.time(),
                    })
                    await self._broadcast(room, peer.peer_id, {
                        "type": "user_reconnected",
                        "peer_id": peer.peer_id,
                        "nick": peer.nick,
                        "peers_count": len(room.connected_peers()),
                    })
                    logger.info(f"[SIGNALING] Peer {peer.peer_id} rejoined {room_id}")
                    return

            if not room:
                await self._send(peer.ws, {"type": "error", "msg": "room_not_found"})
                return
            if room.is_full():
                await self._send(peer.ws, {"type": "error", "msg": "room_full"})
                return

            peer.room_id = room_id
            peer.role = "guest"
            peer.nick = msg.get("nick", peer.peer_id[:6])
            room.peers[peer.peer_id] = peer

            await self._broadcast(room, peer.peer_id, {
                "type": "peer_joined",
                "peer_id": peer.peer_id,
                "nick": peer.nick,
                "peers_count": len(room.connected_peers()),
            })
            await self._send(peer.ws, {
                "type": "room_joined",
                "room_id": room_id,
                "peer_id": peer.peer_id,
                "role": "guest",
                "movie_url": room.movie_url,
                "movie_title": room.movie_title,
                "is_playing": room.is_playing,
                "position_sec": room.get_position(),
                "peers_count": len(room.connected_peers()),
                "democracy_mode": room.democracy_mode,
                "server_timestamp": time.time(),
            })

        # ── WebRTC signaling ──────────────────────────────────────────────────────
        elif t in ("offer", "answer", "ice_candidate"):
            if room:
                await self._relay(peer, msg)

        # ── Player sync ──────────────────────────────────────────────────────────
        elif t == "sync":
            if not room:
                return
            action = msg.get("action")
            position = float(msg.get("position_sec", 0.0))

            can_control = peer.role in ("host", "admin")

            if not can_control:
                if room.democracy_mode:
                    await self._handle_vote_request(room, peer, action, position)
                else:
                    await self._send(peer.ws, {"type": "error", "msg": "permission_denied"})
                return

            await self._apply_sync(room, peer.peer_id, action, position)

        # ── Role management ──────────────────────────────────────────────────────
        elif t == "promote_to_admin":
            if not room or peer.role != "host":
                await self._send(peer.ws, {"type": "error", "msg": "only_host_can_promote"})
                return
            target = room.peers.get(msg.get("target_peer_id", ""))
            if not target:
                await self._send(peer.ws, {"type": "error", "msg": "peer_not_found"})
                return
            target.role = "admin"
            room.admin_ids.add(target.peer_id)
            await self._broadcast_all(room, {
                "type": "user_promoted",
                "peer_id": target.peer_id,
                "nick": target.nick,
                "new_role": "admin",
            })

        elif t == "demote_admin":
            if not room or peer.role != "host":
                await self._send(peer.ws, {"type": "error", "msg": "only_host_can_demote"})
                return
            target = room.peers.get(msg.get("target_peer_id", ""))
            if not target:
                await self._send(peer.ws, {"type": "error", "msg": "peer_not_found"})
                return
            target.role = "guest"
            room.admin_ids.discard(target.peer_id)
            await self._broadcast_all(room, {
                "type": "user_demoted",
                "peer_id": target.peer_id,
                "nick": target.nick,
                "new_role": "guest",
            })

        elif t == "toggle_democracy":
            if not room or peer.role != "host":
                await self._send(peer.ws, {"type": "error", "msg": "only_host"})
                return
            room.democracy_mode = not room.democracy_mode
            await self._broadcast_all(room, {
                "type": "democracy_mode_changed",
                "enabled": room.democracy_mode,
            })

        # ── Vote ─────────────────────────────────────────────────────────────────
        elif t == "vote":
            if not room or not room.democracy_mode or not room.active_vote:
                await self._send(peer.ws, {"type": "error", "msg": "no_active_vote"})
                return
            if msg.get("vote_for", True):
                room.active_vote.setdefault("voters", set()).add(peer.peer_id)
                await self._check_vote_threshold(room)

        # ── Chat ─────────────────────────────────────────────────────────────────
        elif t == "chat":
            if room:
                text = msg.get("text", "")[:300]
                from_nick = msg.get("from", peer.nick or peer.peer_id[:6])[:32]
                await self._broadcast(room, peer.peer_id, {
                    "type": "chat",
                    "from_peer": peer.peer_id,
                    "from": from_nick,
                    "text": text,
                })
                await self._send(peer.ws, {
                    "type": "chat",
                    "from_peer": peer.peer_id,
                    "from": from_nick,
                    "text": text,
                    "own": True,
                })

        # ── Lobby ────────────────────────────────────────────────────────────────
        elif t == "start_video":
            if not room or peer.role != "host":
                await self._send(peer.ws, {"type": "error", "msg": "only_host"})
                return
            room.state = "playing"
            room.movie_url = msg.get("video_url", "")
            room.movie_title = msg.get("video_title", "")
            room.is_playing = True
            room.paused_at = 0.0
            room.timeline_start = time.time()
            await self._broadcast_all(room, {
                "type": "room_state_changed",
                "new_state": "playing",
                "video_url": room.movie_url,
                "video_title": room.movie_title,
            })

        elif t == "ping":
            await self._send(peer.ws, {"type": "pong"})

    # ── Sync helpers ─────────────────────────────────────────────────────────────

    async def _apply_sync(self, room: Room, sender_id: str, action: str, position: float):
        now = time.time()
        if action == "play":
            room.is_playing = True
            room.paused_at = position
            room.timeline_start = now
            room.position_sec = position
        elif action == "pause":
            room.is_playing = False
            room.paused_at = position
            room.position_sec = position
        elif action == "seek":
            room.paused_at = position
            room.position_sec = position
            if room.is_playing:
                room.timeline_start = now

        await self._broadcast(room, sender_id, {
            "type": "sync",
            "action": action,
            "position_sec": position,
            "from_peer": sender_id,
            "server_timestamp": now,
        })

    # ── Democracy / Voting ────────────────────────────────────────────────────────

    async def _handle_vote_request(self, room: Room, peer: Peer, action: str, position: float):
        if room.active_vote and room.active_vote.get("action") == action:
            room.active_vote.setdefault("voters", set()).add(peer.peer_id)
            await self._check_vote_threshold(room)
            return

        if room.vote_task and not room.vote_task.done():
            room.vote_task.cancel()

        room.active_vote = {
            "action": action,
            "position": position,
            "voters": {peer.peer_id},
        }
        await self._broadcast_all(room, {
            "type": "vote_started",
            "action": action,
            "timeout": VOTE_TIMEOUT_SECONDS,
            "from_peer": peer.peer_id,
            "from_nick": peer.nick or peer.peer_id[:6],
        })
        room.vote_task = asyncio.create_task(self._vote_timeout(room, VOTE_TIMEOUT_SECONDS))
        await self._check_vote_threshold(room)

    async def _check_vote_threshold(self, room: Room):
        if not room.active_vote:
            return
        connected = len(room.connected_peers())
        needed = connected // 2 + 1
        current = len(room.active_vote.get("voters", set()))

        await self._broadcast_all(room, {
            "type": "vote_update",
            "action": room.active_vote["action"],
            "votes_current": current,
            "votes_needed": needed,
        })

        if current >= needed:
            action = room.active_vote["action"]
            position = room.active_vote["position"]
            if room.vote_task and not room.vote_task.done():
                room.vote_task.cancel()
            room.active_vote = {}
            await self._broadcast_all(room, {"type": "vote_passed", "action": action})
            await self._apply_sync(room, "vote", action, position)

    async def _vote_timeout(self, room: Room, seconds: int):
        await asyncio.sleep(seconds)
        if room.active_vote:
            action = room.active_vote.get("action", "")
            room.active_vote = {}
            await self._broadcast_all(room, {"type": "vote_failed", "action": action, "reason": "timeout"})

    # ── WebRTC relay ─────────────────────────────────────────────────────────────

    async def _relay(self, from_peer: Peer, msg: dict):
        room = self._get_peer_room(from_peer)
        if not room:
            return
        target = room.peers.get(msg.get("to", ""))
        if target and target.status == "connected":
            msg["from"] = from_peer.peer_id
            await self._send(target.ws, msg)

    # ── Broadcast helpers ─────────────────────────────────────────────────────────

    async def _broadcast(self, room: Room, exclude_id: str, msg: dict):
        for peer in room.other_connected(exclude_id):
            await self._send(peer.ws, msg)

    async def _broadcast_all(self, room: Room, msg: dict):
        for peer in room.connected_peers():
            await self._send(peer.ws, msg)

    # ── Disconnect / offline ─────────────────────────────────────────────────────

    async def _on_disconnect(self, peer: Peer):
        logger.info(f"[SIGNALING] Peer disconnected: {peer.peer_id}")
        room = self._get_peer_room(peer)

        if room:
            peer.status = "offline"
            peer.offline_since = time.time()
            connected = room.connected_peers()

            await self._broadcast(room, peer.peer_id, {
                "type": "peer_left",
                "peer_id": peer.peer_id,
                "nick": peer.nick,
                "peers_count": len(connected),
                "will_rejoin": True,
            })

            # Transfer host if host disconnected
            if peer.peer_id == room.host_id and connected:
                new_host = connected[0]
                new_host.role = "host"
                room.host_id = new_host.peer_id
                await self._broadcast_all(room, {
                    "type": "host_transferred",
                    "new_host_id": new_host.peer_id,
                    "new_host_nick": new_host.nick,
                })

            asyncio.create_task(
                self._cleanup_offline_peer(room.room_id, peer.peer_id, OFFLINE_GRACE_SECONDS)
            )
        else:
            self._peers.pop(peer.peer_id, None)

    async def _cleanup_offline_peer(self, room_id: str, peer_id: str, delay: float):
        await asyncio.sleep(delay)
        room = self._rooms.get(room_id)
        if not room:
            return
        peer = room.peers.get(peer_id)
        if peer and peer.status == "offline":
            room.peers.pop(peer_id, None)
            self._peers.pop(peer_id, None)
            logger.info(f"[SIGNALING] Offline peer {peer_id} removed from {room_id}")
            await self._broadcast_all(room, {
                "type": "peer_left",
                "peer_id": peer_id,
                "peers_count": len(room.connected_peers()),
                "will_rejoin": False,
            })
            if not room.peers:
                self._rooms.pop(room_id, None)
                logger.info(f"[SIGNALING] Empty room {room_id} removed")

    def _get_peer_room(self, peer: Peer) -> Optional[Room]:
        if peer.room_id:
            return self._rooms.get(peer.room_id)
        return None

    @staticmethod
    async def _send(ws: WebSocket, data: dict):
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[SIGNALING] Send failed: {e}")


_manager = SignalingManager()


def get_signaling() -> SignalingManager:
    return _manager
