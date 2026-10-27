"""Serial integration. Importing this package never opens a port."""

from .hardware import HardwareRig

__all__ = ["HardwareRig"]
