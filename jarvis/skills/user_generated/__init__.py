"""User-generated skills directory.

This package is the *only* location where SkillForge writes generated
skill files.  Deleting the entire ``user_generated/`` folder is safe —
Jarvis continues working with its built-in tools, and the directory is
recreated automatically when the next skill is approved.

Each skill is a standalone ``.py`` file following a strict contract:
- Imports limited to the sandbox whitelist.
- No direct access to jarvis internals (core, memory, ui, config, DB).
- Tools are invoked through the read-only registry reference passed at
  runtime, never by importing tool modules directly.
"""
