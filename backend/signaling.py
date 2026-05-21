"""
signaling.py — WebSocket сигнальный сервер для WebRTC
Комнаты: создание, присоединение, relay SDP/ICE между участниками
Синхронизация: play/pause/seek через DataChannel-подобные сообщения
"""

import json
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("kinovibe.signaling")


@dataclass
class Peer:
    peer_id: str
    ws: WebSocket
    room_id: Optional[str] = None


@dataclass
class Room:
    room_id: str
    movie_url: str = ""
    movie_title: str = ""
    peers: dict[str, Peer] = field(default_factory=dict)
    # Состояние плеера (синхронизация)
    is_playing: bool = False
    position_sec: float = 0.0

    def is_full(self) -> bool:
        return len(self.peers) >= 8  # максимум 8 участников

    def other_peers(self, exclude_id: str) -> list[Peer]:
        return [p for pid, p in self.peers.items() if pid != exclude_id]


class SignalingManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}
        self._peers: dict[str, Peer] = {}

    # ─── Комнаты ──────────────────────────────────────────────────────────────

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
                "peers": len(r.peers),
            }
            for r in self._rooms.values()
        ]

    # ─── WebSocket хендлер ────────────────────────────────────────────────────

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
                    await self._send(ws, {"type": "error", "msg": "invalid json"})
                    continue

                await self._dispatch(peer, msg)

        except WebSocketDisconnect:
            await self._on_disconnect(peer)

    async def _dispatch(self, peer: Peer, msg: dict):
        t = msg.get("type")

        if t == "create_room":
            room_id = self.create_room(
                movie_url=msg.get("movie_url", ""),
                movie_title=msg.get("movie_title", ""),
            )
            peer.room_id = room_id
            room = self._rooms[room_id]
            room.peers[peer.peer_id] = peer
            await self._send(peer.ws, {
                "type": "room_created",
                "room_id": room_id,
                "peer_id": peer.peer_id,
            })

        elif t == "join_room":
            room_id = msg.get("room_id", "").upper()
            room = self._rooms.get(room_id)
            if not room:
                await self._send(peer.ws, {"type": "error", "msg": "room_not_found"})
                return
            if room.is_full():
                await self._send(peer.ws, {"type": "error", "msg": "room_full"})
                return
            peer.room_id = room_id
            room.peers[peer.peer_id] = peer
            # Уведомить остальных о новом участнике
            await self._broadcast(room, peer.peer_id, {
                "type": "peer_joined",
                "peer_id": peer.peer_id,
                "peers_count": len(room.peers),
            })
            # Отправить новому участнику текущее состояние плеера
            await self._send(peer.ws, {
                "type": "room_joined",
                "room_id": room_id,
                "peer_id": peer.peer_id,
                "movie_url": room.movie_url,
                "movie_title": room.movie_title,
                "is_playing": room.is_playing,
                "position_sec": room.position_sec,
                "peers_count": len(room.peers),
            })

        elif t == "offer":
            await self._relay(peer, msg)

        elif t == "answer":
            await self._relay(peer, msg)

        elif t == "ice_candidate":
            await self._relay(peer, msg)

        elif t == "sync":
            # Синхронизация плеера: play/pause/seek
            room = self._get_peer_room(peer)
            if not room:
                return
            action = msg.get("action")  # "play" | "pause" | "seek"
            position = msg.get("position_sec", 0.0)
            if action == "play":
                room.is_playing = True
                room.position_sec = position
            elif action == "pause":
                room.is_playing = False
                room.position_sec = position
            elif action == "seek":
                room.position_sec = position
            # Броадкаст всем кроме отправителя
            await self._broadcast(room, peer.peer_id, {
                "type": "sync",
                "action": action,
                "position_sec": position,
                "from_peer": peer.peer_id,
            })

        elif t == "chat":
            room = self._get_peer_room(peer)
            if room:
                await self._broadcast(room, peer.peer_id, {
                    "type": "chat",
                    "from_peer": peer.peer_id,
                    "text": msg.get("text", "")[:300],
                })

        elif t == "ping":
            await self._send(peer.ws, {"type": "pong"})

    async def _relay(self, from_peer: Peer, msg: dict):
        """Передать SDP/ICE конкретному участнику."""
        to_id = msg.get("to")
        room = self._get_peer_room(from_peer)
        if not room:
            return
        target = room.peers.get(to_id)
        if target:
            msg["from"] = from_peer.peer_id
            await self._send(target.ws, msg)

    async def _broadcast(self, room: Room, exclude_id: str, msg: dict):
        for peer in room.other_peers(exclude_id):
            await self._send(peer.ws, msg)

    async def _on_disconnect(self, peer: Peer):
        logger.info(f"[SIGNALING] Peer disconnected: {peer.peer_id}")
        self._peers.pop(peer.peer_id, None)
        room = self._get_peer_room(peer)
        if room:
            room.peers.pop(peer.peer_id, None)
            await self._broadcast(room, peer.peer_id, {
                "type": "peer_left",
                "peer_id": peer.peer_id,
                "peers_count": len(room.peers),
            })
            # Удалить пустую комнату
            if not room.peers:
                self._rooms.pop(room.room_id, None)
                logger.info(f"[SIGNALING] Room removed: {room.room_id}")

    def _get_peer_room(self, peer: Peer) -> Optional[Room]:
        if peer.room_id:
            return self._rooms.get(peer.room_id)
        return None

    @staticmethod
    async def _send(ws: WebSocket, data: dict):
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[SIGNALING] Send failed: {e}")


# Singleton
_manager = SignalingManager()


def get_signaling() -> SignalingManager:
    return _manager
