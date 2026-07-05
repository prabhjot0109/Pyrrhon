"""Public plugin-loader API (M7)."""

from pyrrhon.plugins.loader import (
    LoadedPlugin,
    PluginContributes,
    PluginManager,
    PluginManifest,
    merge_plugin_settings,
    parse_manifest,
    read_trusted,
    record_trusted,
)

__all__ = [
    "LoadedPlugin",
    "PluginContributes",
    "PluginManager",
    "PluginManifest",
    "merge_plugin_settings",
    "parse_manifest",
    "read_trusted",
    "record_trusted",
]
