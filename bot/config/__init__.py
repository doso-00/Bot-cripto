"""Caricamento e validazione di YAML + .env; espone un oggetto Settings tipizzato."""

from bot.config.settings import PROJECT_ROOT, Settings, load_settings

__all__ = ["PROJECT_ROOT", "Settings", "load_settings"]
