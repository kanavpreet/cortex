"""Continuous production audits.

Each audit type declares its own ``AuditSpec`` (see ``sheet_sync.py``) and hands
it directly to its own ``main.py`` — there's exactly one audit type today, so
there's no registry indirection to maintain until a second one actually shows up.
"""
