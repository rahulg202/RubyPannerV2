"""Adapter/infrastructure layer: all file I/O and format-specific code.

This is the only layer allowed to import openpyxl and to touch byte streams or
the filesystem. Adapters implement the port protocols declared in
``services.ports`` and depend only on domain types, never on services or UI.

Note: named ``io_adapters`` rather than ``io`` to avoid shadowing the Python
standard library ``io`` module.
"""
