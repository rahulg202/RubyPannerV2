"""Application/service layer: use-case orchestration.

Each service coordinates the domain layer and I/O adapters to fulfil one
user-facing workflow. Services depend on port protocols (see ports.py), not on
concrete adapter implementations.
"""
