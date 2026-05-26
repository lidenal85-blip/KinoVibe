from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SearchResult:
    id: str
    title: str
    url: str
    provider: str
    source_type: str       # "video" | "embed" | "magnet"
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    channel: Optional[str] = None
    description: Optional[str] = None
    source_title: Optional[str] = None  # original torrent/source filename
    source_url: Optional[str] = None    # direct link to tracker page
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "id":           self.id,
            "title":        self.title,
            "url":          self.url,
            "thumbnail":    self.thumbnail,
            "duration":     self.duration,
            "channel":      self.channel,
            "provider":     self.provider,
            "source_type":  self.source_type,
            "description":  self.description,
        }
        if self.source_title:
            d["source_title"] = self.source_title
        if self.source_url:
            d["source_url"] = self.source_url
        if self.extra:
            d.update(self.extra)
        return d


@dataclass
class StreamInfo:
    stream_url: str
    provider: str
    protocol: str  # "http" | "hls" | "magnet" | "embed"
    audio_url: Optional[str] = None
    embed_url: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "stream_url": self.stream_url,
            "provider": self.provider,
            "protocol": self.protocol,
        }
        if self.embed_url:
            d["embed_url"] = self.embed_url
        if self.audio_url:
            d["audio_url"] = self.audio_url
        if self.extra:
            d["extra"] = self.extra
        return d


class BaseProvider(ABC):
    name: str = "base"
    source_type: str = "video"

    @property
    def enabled(self) -> bool:
        return True

    @abstractmethod
    async def search(self, query: str, category: str) -> list[SearchResult]:
        ...

    @abstractmethod
    async def get_stream(self, url: str) -> StreamInfo:
        ...

    def to_dict(self) -> dict:
        return {"name": self.name, "enabled": self.enabled, "source_type": self.source_type}
