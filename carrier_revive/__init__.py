"""carrier_revive: revive dead Codex carrier sessions against their work trees.

Read-only over ~/.codex/sessions. Never kills processes. Writes only its own
log file inside the target cwd when --detach is used.
"""

__version__ = "0.1.0"

