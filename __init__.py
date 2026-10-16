"""Cable Explode QGIS plugin.

classFactory is the QGIS entry point. The offset math does not import QGIS.
"""


def classFactory(iface):
    from .plugin import CableExplodePlugin

    return CableExplodePlugin(iface)
