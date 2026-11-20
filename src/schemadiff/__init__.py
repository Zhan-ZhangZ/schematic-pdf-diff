"""Offline schematic PDF comparison."""

from .config import CompareConfig
from .pipeline import compare_documents

__all__ = ["CompareConfig", "compare_documents"]

__version__ = "0.1.2"
