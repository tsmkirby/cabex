"""Offscreen checks that the exploded tools pick the drawn line, not the stored one.

Run with the Python bundled with QGIS; skipped elsewhere.
"""

import os
import unittest
from unittest.mock import patch

try:
    from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsRectangle, QgsVectorLayer
    from qgis.gui import QgsMapCanvas, QgsMapMouseEvent
    from qgis.PyQt.QtCore import QEvent, QPoint, Qt
    from qgis.PyQt.QtWidgets import QMainWindow, QToolBar
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


class _Bar:
    def __init__(self):
        self.messages = []

    def __getattr__(self, name):
        return lambda *args: self.messages.append((name, args))


class _Iface:
    def __init__(self, canvas):
        self.window = QMainWindow()
        self.canvas = canvas
        self.bar = _Bar()
        self.status = _Bar()
        self.forms = []

    def mainWindow(self):
        return self.window

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return self.bar

    def statusBarIface(self):
        return self.status

    def addToolBar(self, title):
        toolbar = QToolBar(title, self.window)
        self.window.addToolBar(toolbar)
        return toolbar

    def addDockWidget(self, area, dock):
        self.window.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.window.removeDockWidget(dock)

    def openFeatureForm(self, layer, feature, update_only=False, modal=True):
        self.forms.append((layer.id(), feature.id()))


@unittest.skipUnless(HAS_QGIS, 'Requires the QGIS Python runtime')
class ExplodedToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        cls.app = QgsApplication.instance() or QgsApplication([], True)
        cls.app.initQgis()

    def setUp(self):
        from cable_explode.plugin import CableExplodePlugin
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem('EPSG:3857'))
        self.layer = QgsVectorLayer('LineString?crs=EPSG:3857&field=cable_id:string', 'Cables', 'memory')
        for name in ('A', 'B'):
            feature = QgsFeature(self.layer.fields())
            feature['cable_id'] = name
            feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(100, 0)]))
            self.layer.dataProvider().addFeatures([feature])
        project.addMapLayer(self.layer)
        self.ids = {f['cable_id']: f.id() for f in self.layer.getFeatures()}
        self.canvas = QgsMapCanvas()
        self.canvas.resize(800, 400)
        self.canvas.setDestinationCrs(project.crs())
        self.canvas.setLayers([self.layer])
        self.canvas.setExtent(QgsRectangle(40, -2, 60, 2))  # about 2.5 cm per pixel
        self.canvas.show()
        self.app.processEvents()
        self.iface = _Iface(self.canvas)
        self.plugin = CableExplodePlugin(self.iface)
        self.patches = [patch.object(self.plugin, '_participating', lambda layers=None: [self.layer]),
                        patch.object(self.plugin, '_gap', lambda: 0.2),
                        patch.object(self.plugin, '_min_overlap', lambda: 1.0)]
        for item in self.patches:
            item.start()
        self.plugin.initGui()

    def tearDown(self):
        self.plugin._toggle.setChecked(False)
        self.plugin.unload()
        for item in self.patches:
            item.stop()
        QgsProject.instance().clear()
        self.canvas.deleteLater()
        self.iface.window.deleteLater()

    def pixel(self, x, y):
        point = self.canvas.getCoordinateTransform().transform(x, y)
        return QPoint(round(point.x()), round(point.y()))

    def click(self, tool, x, y, modifiers=None):
        modifiers = Qt.NoModifier if modifiers is None else modifiers
        for kind, handler in ((QEvent.MouseButtonPress, tool.canvasPressEvent),
                              (QEvent.MouseButtonRelease, tool.canvasReleaseEvent)):
            handler(QgsMapMouseEvent(self.canvas, kind, self.pixel(x, y), Qt.LeftButton, Qt.LeftButton, modifiers))

    def drag(self, tool, start, end, modifiers=None):
        modifiers = Qt.NoModifier if modifiers is None else modifiers
        tool.canvasPressEvent(QgsMapMouseEvent(self.canvas, QEvent.MouseButtonPress, self.pixel(*start),
                                               Qt.LeftButton, Qt.LeftButton, modifiers))
        tool.canvasMoveEvent(QgsMapMouseEvent(self.canvas, QEvent.MouseMove, self.pixel(*end),
                                              Qt.NoButton, Qt.LeftButton, modifiers))
        tool.canvasReleaseEvent(QgsMapMouseEvent(self.canvas, QEvent.MouseButtonRelease, self.pixel(*end),
                                                 Qt.LeftButton, Qt.NoButton, modifiers))

    def zoom_out(self):
        # About 12 cm per pixel: the click radius now covers the whole two-cable stack.
        self.canvas.setExtent(QgsRectangle(0, -25, 100, 25))
        self.app.processEvents()

    def selected(self):
        names = {fid: name for name, fid in self.ids.items()}
        return sorted(names[fid] for fid in self.layer.selectedFeatureIds())

    def test_click_selects_the_drawn_cable_and_cycles_through_the_stack(self):
        self.plugin._toggle.setChecked(True)
        sources = self.plugin.pick_sources()
        self.assertEqual(len(sources[0][1]), 2)  # both cables are drawn apart
        self.zoom_out()
        tool = self.plugin._select_tool
        self.click(tool, 50, 0.1)  # B is drawn 0.1 m north of the stored line
        self.assertEqual(self.selected(), ['B'])
        self.click(tool, 50, 0.1)
        self.assertEqual(self.selected(), ['A'])
        self.click(tool, 50, -0.1)  # a different stack order resets to the nearest
        self.click(tool, 50, -0.1)
        self.assertEqual(len(self.selected()), 1)
        before = set(self.selected())
        self.click(tool, 50, 0.1, Qt.ShiftModifier)  # toggles the nearest cable, B
        self.assertEqual(set(self.selected()), before ^ {'B'})
        self.click(tool, 50, 0.1, Qt.ShiftModifier)
        self.assertEqual(set(self.selected()), before)
        self.click(tool, 50, 20)  # nothing drawn there
        self.assertEqual(self.selected(), [])

    def test_box_uses_the_drawn_line_and_respects_modifiers(self):
        tool = self.plugin._select_tool
        self.drag(tool, (45, 0.05), (55, 0.3))
        self.assertEqual(self.selected(), [])  # explode off: both cables are at y = 0
        self.plugin._toggle.setChecked(True)
        self.drag(tool, (45, 0.05), (55, 0.3))
        self.assertEqual(self.selected(), ['B'])
        self.drag(tool, (45, -0.3), (55, -0.05), Qt.ShiftModifier)
        self.assertEqual(self.selected(), ['A', 'B'])
        self.drag(tool, (45, 0.05), (55, 0.3), Qt.ControlModifier)
        self.assertEqual(self.selected(), ['A'])
        self.drag(tool, (45, -0.3), (55, 0.3), Qt.ControlModifier | Qt.ShiftModifier)
        self.assertEqual(self.selected(), ['A'])

    def test_identify_lists_the_stack_nearest_first_and_acts_on_one(self):
        self.plugin._toggle.setChecked(True)
        panel = self.plugin._panel
        self.zoom_out()
        self.click(self.plugin._identify_tool, 50, 0.1)
        self.assertEqual(panel.tree.topLevelItemCount(), 2)
        self.assertEqual(panel.tree.topLevelItem(0).text(0), 'B')
        self.assertIsNotNone(panel._band)
        panel.tree.setCurrentItem(panel.tree.topLevelItem(1))
        panel.select_button.click()
        self.assertEqual(self.selected(), ['A'])
        panel.form_button.click()
        self.assertEqual(self.iface.forms, [(self.layer.id(), self.ids['A'])])
        panel.zoom_button.click()
        QgsProject.instance().removeMapLayer(self.layer.id())
        self.assertEqual(panel.tree.topLevelItemCount(), 0)
        panel.form_button.click()  # nothing left to act on; must not raise
        self.assertEqual(len(self.iface.forms), 1)

    def test_toolbar_buttons_are_square_icons_with_accessible_names(self):
        self.assertEqual(len(self.plugin._actions), 4)
        for action in self.plugin._actions:
            self.assertFalse(action.icon().pixmap(24, 24).isNull(), action.text())
            self.assertTrue(action.toolTip().startswith(action.text()))
            button = self.plugin.toolbar.widgetForAction(action)
            self.assertEqual((button.width(), button.height()), (32, 32))
            self.assertEqual(button.accessibleName(), action.text())


if __name__ == '__main__':
    unittest.main()
