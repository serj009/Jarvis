"""Spotify integration — play/pause/next/search/playlist.

Off by default. Uses Spotify OAuth2 via the ``spotipy`` library.
"""

from __future__ import annotations

import logging

from jarvis.integrations.base import BaseIntegration

log = logging.getLogger(__name__)


class SpotifyIntegration(BaseIntegration):
    name = "spotify"
    display_name = "Spotify"
    category = "media"
    description = "Control Spotify playback, search music, manage playlists."
    icon = "\U0001f3b5"

    auth_type = "oauth2"
    auth_fields = [
        {"name": "client_id", "label": "Client ID", "type": "text"},
        {"name": "client_secret", "label": "Client Secret", "type": "password"},
    ]
    required_packages = ["spotipy"]

    auth: object | None = None
    _sp = None

    async def connect(self) -> bool:
        if self.auth is None:
            self.connected = False
            return False
        tokens = self.auth.get_tokens(self.name)
        if not tokens or not tokens.get("access_token"):
            self.connected = False
            return False
        try:
            import spotipy  # noqa: E402
            self._sp = spotipy.Spotify(auth=tokens["access_token"])
            self._sp.current_user()
            self.connected = True
        except Exception:
            self.connected = False
        return self.connected

    async def disconnect(self) -> None:
        self._sp = None
        self.connected = False

    async def health_check(self) -> bool:
        if self._sp is None:
            return False
        try:
            self._sp.current_user()
            return True
        except Exception:
            return False

    # -- tools -----------------------------------------------------------

    def get_tools(self) -> list[dict]:
        return [
            _fn("spotify_play", "Play a track, playlist, or artist.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("spotify_pause", "Pause playback.", {}),
            _fn("spotify_next", "Skip to next track.", {}),
            _fn("spotify_prev", "Go to previous track.", {}),
            _fn("spotify_current", "Show what's currently playing.", {}),
            _fn("spotify_search", "Search for tracks.",
                {"query": {"type": "string"}}, required=["query"]),
            _fn("spotify_playlist", "Create a playlist or add a track.",
                {"name": {"type": "string"}, "track_uri": {"type": "string"}},
                required=["name"]),
        ]

    def get_voice_patterns(self) -> list[tuple]:
        return [
            (r"^play\s+(?:some\s+)?(.+)$", "spotify_play", 300),
            (r"^\u0432\u043a\u043b\u044e\u0447\u0438\s+(.+)$", "spotify_play", 300),
            (r"^what(?:'s|\s+is)\s+playing$", "spotify_current", 310),
            (r"^\u0447\u0442\u043e\s+\u0438\u0433\u0440\u0430\u0435\u0442$", "spotify_current", 310),
            (r"^\u0449\u043e\s+\u0433\u0440\u0430\u0454$", "spotify_current", 310),
            (r"^next\s+(?:song|track)$", "spotify_next", 320),
            (r"^\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0430\u044f\s+\u043f\u0435\u0441\u043d\u044f$", "spotify_next", 320),
            (r"^\u043d\u0430\u0441\u0442\u0443\u043f\u043d\u0430\s+\u043f\u0456\u0441\u043d\u044f$", "spotify_next", 320),
            (r"^pause\s*(?:music)?$", "spotify_pause", 320),
            (r"^\u043f\u0430\u0443\u0437\u0430$", "spotify_pause", 320),
        ]

    async def execute_tool(self, tool_name: str, args: dict) -> str:
        if self._sp is None:
            return "Spotify is not connected, sir."
        try:
            if tool_name == "spotify_play":
                return self._play(args)
            if tool_name == "spotify_pause":
                self._sp.pause_playback()
                return "Paused, sir."
            if tool_name == "spotify_next":
                self._sp.next_track()
                return "Next track, sir."
            if tool_name == "spotify_prev":
                self._sp.previous_track()
                return "Previous track, sir."
            if tool_name == "spotify_current":
                return self._current()
            if tool_name == "spotify_search":
                return self._search(args)
            if tool_name == "spotify_playlist":
                return self._playlist(args)
        except Exception as e:
            log.warning("[spotify] %s failed", tool_name, exc_info=True)
            return f"Spotify error, sir: {e}"
        return f"Unknown tool: {tool_name}"

    # -- internals -------------------------------------------------------

    def _play(self, args: dict) -> str:
        query = args.get("query", "")
        results = self._sp.search(q=query, limit=1, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            return f"No tracks found for '{query}', sir."
        track = tracks[0]
        self._sp.start_playback(uris=[track["uri"]])
        return f"Playing '{track['name']}' by {track['artists'][0]['name']}, sir."

    def _current(self) -> str:
        current = self._sp.current_playback()
        if not current or not current.get("item"):
            return "Nothing is playing, sir."
        item = current["item"]
        artist = item["artists"][0]["name"] if item.get("artists") else "Unknown"
        return f"Now playing: '{item['name']}' by {artist}, sir."

    def _search(self, args: dict) -> str:
        query = args.get("query", "")
        results = self._sp.search(q=query, limit=5, type="track")
        tracks = results.get("tracks", {}).get("items", [])
        if not tracks:
            return f"No results for '{query}', sir."
        lines = [f"  \u2022 {t['name']} — {t['artists'][0]['name']}" for t in tracks]
        return f"Search results, sir:\n" + "\n".join(lines)

    def _playlist(self, args: dict) -> str:
        name = args.get("name", "Jarvis Playlist")
        track_uri = args.get("track_uri")
        user_id = self._sp.current_user()["id"]
        playlists = self._sp.current_user_playlists(limit=50)
        existing = None
        for pl in playlists.get("items", []):
            if pl["name"] == name:
                existing = pl
                break
        if existing is None:
            existing = self._sp.user_playlist_create(user_id, name)
        if track_uri:
            self._sp.playlist_add_items(existing["id"], [track_uri])
            return f"Added track to '{name}', sir."
        return f"Playlist '{name}' ready, sir."


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required or [],
            },
        },
    }
