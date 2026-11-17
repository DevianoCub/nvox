"""Optional external-tool bridges.

Every plugin in this package declares its binary in ``requires``. The engine
checks requirements before running a plugin, so a missing tool becomes a
recorded requirement skip instead of a failed scan.
"""
