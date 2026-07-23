"""Read-only source registration and metadata inventory."""

from scene.inventory.registry import SourceDescriptor, SourceRegistry
from scene.inventory.scanner import InventoryScan, scan_inventory

__all__ = [
    "InventoryScan",
    "SourceDescriptor",
    "SourceRegistry",
    "scan_inventory",
]
