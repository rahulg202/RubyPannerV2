"""Domain layer: pure business logic with no I/O or UI dependencies.

Modules in this package are deterministic and side-effect free, making them
directly unit- and property-testable. They must not import Streamlit, openpyxl,
or perform any file/network I/O.
"""
