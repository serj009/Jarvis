"""Password generator — random passwords and passphrases.

Generates cryptographically secure passwords using the stdlib ``secrets``
module. Supports:
- Random character passwords with configurable length and character sets
- Passphrase mode: "correct-horse-battery-staple" style (N random words)
- Automatic clipboard copy
- Exclusion of ambiguous characters (0/O, l/1/I)

No external dependencies — uses only ``secrets`` and ``string`` from stdlib.
"""

from __future__ import annotations

import logging
import string
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from jarvis.tools.registry import ToolResult, VoicePattern

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in word list for passphrases (compact — no file dependency)
# ---------------------------------------------------------------------------
# A curated list of common, easy-to-type English words (4-8 letters).
# In production this could be loaded from EFF's diceware list, but
# embedding a small list here avoids network/file dependencies.

_PASSPHRASE_WORDS: list[str] = [
    "apple", "baker", "candy", "dance", "eagle", "flame", "grace", "happy",
    "ivory", "jolly", "karma", "lemon", "magic", "noble", "ocean", "piano",
    "queen", "river", "stone", "tiger", "unity", "vivid", "water", "xenon",
    "yacht", "zebra", "amber", "blaze", "charm", "dream", "ember", "frost",
    "ghost", "hover", "index", "jewel", "knack", "lunar", "merge", "nerve",
    "orbit", "pearl", "quiet", "robin", "solar", "torch", "ultra", "vigor",
    "whale", "oxide", "yield", "zonal", "brave", "cloud", "delta", "elbow",
    "forum", "globe", "heart", "laser", "maple", "north", "omega", "prism",
    "quest", "radar", "steel", "trend", "urban", "valve", "width", "spark",
    "grain", "brick", "craft", "dense", "exact", "fiber", "gleam", "haste",
    "image", "joint", "knife", "light", "metal", "novel", "olive", "pixel",
    "rapid", "shelf", "tower", "union", "vault", "world", "arena", "badge",
    "cable", "diary", "event", "field", "guide", "honey", "input", "lunar",
]

# Ambiguous character sets
_AMBIGUOUS = set("0OoIl1|")


# ---------------------------------------------------------------------------
# Args model
# ---------------------------------------------------------------------------

class GeneratePasswordArgs(BaseModel):
    mode: Literal["random", "passphrase"] = Field(
        default="random",
        description=(
            "random: character-based password; "
            "passphrase: word-based 'correct-horse-battery-staple' style."
        ),
    )
    length: int = Field(
        default=16,
        description=(
            "For random mode: number of characters (8-128). "
            "For passphrase mode: number of words (3-10)."
        ),
        ge=3,
        le=128,
    )
    use_uppercase: bool = Field(
        default=True,
        description="Include uppercase letters in random mode.",
    )
    use_digits: bool = Field(
        default=True,
        description="Include digits in random mode.",
    )
    use_special: bool = Field(
        default=True,
        description="Include special characters (!@#$%^&*) in random mode.",
    )
    exclude_ambiguous: bool = Field(
        default=False,
        description="Exclude visually ambiguous characters (0/O, l/1/I).",
    )
    separator: str = Field(
        default="-",
        description="Word separator for passphrase mode.",
    )
    copy_to_clipboard: bool = Field(
        default=True,
        description="Automatically copy the password to clipboard.",
    )


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class GeneratePasswordTool:
    name: str = "generate_password"
    description: str = (
        "Generates a secure random password or passphrase and optionally "
        "copies it to the clipboard. Supports configurable length, character "
        "sets, ambiguous character exclusion, and passphrase mode. "
        "(RU: сгенерируй пароль, случайный пароль, придумай пароль / "
        "UK: згенеруй пароль, випадковий пароль, придумай пароль)"
    )
    args_schema = GeneratePasswordArgs
    requires_confirmation: bool = False

    voice_patterns: ClassVar[tuple[VoicePattern, ...]] = (
        # EN
        VoicePattern(
            regex=r"^generate\s+(?:a\s+)?(?:secure\s+|random\s+)?password(?:\s+(\d+)\s+(?:char(?:acter)?s?|long))?$",
            priority=455,
            args=lambda m: {
                "mode": "random",
                "length": int(m.group(1)) if m.group(1) else 16,
            },
        ),
        VoicePattern(
            regex=r"^(?:create|make)\s+(?:a\s+)?passphrase(?:\s+(\d+)\s+words?)?$",
            priority=455,
            args=lambda m: {
                "mode": "passphrase",
                "length": int(m.group(1)) if m.group(1) else 4,
            },
        ),
        VoicePattern(
            regex=r"^random\s+password\s+(\d+)\s+(?:char(?:acter)?s?|symbols?)$",
            priority=455,
            args=lambda m: {"mode": "random", "length": int(m.group(1))},
        ),
        # RU
        VoicePattern(
            regex=r"^сгенерируй\s+(?:мне\s+)?пароль(?:\s+(\d+)\s+символ\w*)?$",
            priority=455,
            args=lambda m: {
                "mode": "random",
                "length": int(m.group(1)) if m.group(1) else 16,
            },
        ),
        VoicePattern(
            regex=r"^(?:случайный|новый)\s+пароль(?:\s+(\d+)\s+символ\w*)?$",
            priority=455,
            args=lambda m: {
                "mode": "random",
                "length": int(m.group(1)) if m.group(1) else 16,
            },
        ),
        VoicePattern(
            regex=r"^придумай\s+пароль$",
            priority=455,
            args=lambda m: {"mode": "random", "length": 16},
        ),
        # UK
        VoicePattern(
            regex=r"^згенеруй\s+(?:мені\s+)?пароль(?:\s+(\d+)\s+символ\w*)?$",
            priority=455,
            args=lambda m: {
                "mode": "random",
                "length": int(m.group(1)) if m.group(1) else 16,
            },
        ),
        VoicePattern(
            regex=r"^(?:випадковий|новий)\s+пароль(?:\s+(\d+)\s+символ\w*)?$",
            priority=455,
            args=lambda m: {
                "mode": "random",
                "length": int(m.group(1)) if m.group(1) else 16,
            },
        ),
        VoicePattern(
            regex=r"^придумай\s+пароль$",
            priority=456,
            args=lambda m: {"mode": "random", "length": 16},
        ),
    )

    async def execute(self, args: GeneratePasswordArgs) -> ToolResult:
        try:
            if args.mode == "passphrase":
                password = self._generate_passphrase(
                    word_count=args.length,
                    separator=args.separator,
                )
            else:
                password = self._generate_random(
                    length=args.length,
                    use_uppercase=args.use_uppercase,
                    use_digits=args.use_digits,
                    use_special=args.use_special,
                    exclude_ambiguous=args.exclude_ambiguous,
                )
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        clipboard_msg = ""
        if args.copy_to_clipboard:
            clipboard_msg = self._copy_to_clipboard(password)

        mode_label = "passphrase" if args.mode == "passphrase" else "password"
        return ToolResult(
            success=True,
            output=(
                f"Generated {mode_label}: {password}\n"
                f"Length: {len(password)} characters.{clipboard_msg}"
            ),
        )

    @staticmethod
    def _generate_random(
        *,
        length: int,
        use_uppercase: bool,
        use_digits: bool,
        use_special: bool,
        exclude_ambiguous: bool,
    ) -> str:
        import secrets

        if length < 8:
            raise ValueError("Minimum password length is 8 characters.")
        if length > 128:
            raise ValueError("Maximum password length is 128 characters.")

        charset = string.ascii_lowercase
        if use_uppercase:
            charset += string.ascii_uppercase
        if use_digits:
            charset += string.digits
        if use_special:
            charset += "!@#$%^&*()-_=+[]{}|;:,.<>?"

        if exclude_ambiguous:
            charset = "".join(c for c in charset if c not in _AMBIGUOUS)

        if not charset:
            raise ValueError("No characters available with current settings.")

        return "".join(secrets.choice(charset) for _ in range(length))

    @staticmethod
    def _generate_passphrase(*, word_count: int, separator: str) -> str:
        import secrets

        if word_count < 3:
            raise ValueError("Minimum passphrase length is 3 words.")
        if word_count > 10:
            raise ValueError("Maximum passphrase length is 10 words.")

        words = [secrets.choice(_PASSPHRASE_WORDS) for _ in range(word_count)]
        return separator.join(words)

    @staticmethod
    def _copy_to_clipboard(text: str) -> str:
        """Copy text to system clipboard. Returns status message."""
        try:
            import pyperclip
            pyperclip.copy(text)
            return " Copied to clipboard."
        except ImportError:
            pass
        # Fallback: platform-specific
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                process = subprocess.Popen(
                    ["clip"], stdin=subprocess.PIPE, shell=True,
                )
                process.communicate(text.encode("utf-16-le"))
                return " Copied to clipboard."
            elif sys.platform == "darwin":
                process = subprocess.Popen(
                    ["pbcopy"], stdin=subprocess.PIPE,
                )
                process.communicate(text.encode("utf-8"))
                return " Copied to clipboard."
            else:
                process = subprocess.Popen(
                    ["xclip", "-selection", "clipboard"],
                    stdin=subprocess.PIPE,
                )
                process.communicate(text.encode("utf-8"))
                return " Copied to clipboard."
        except Exception:
            log.debug("Clipboard copy failed", exc_info=True)
            return " (Could not copy to clipboard automatically.)"
