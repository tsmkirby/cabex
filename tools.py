"""Select and identify tools that hit-test the exploded lines as drawn.

QGIS's own tools test the stored geometry, which sits under the whole stack.
These tools test the geometry the explode renderer draws instead, so one
cable can be picked out of a stack.
"""

from __future__ import annotations

import traceback

from qgis.PyQt.QtCore import QPoint, QRect, Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsRenderContext,
    QgsUnitTypes,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand

from .core.pick import ADD, REPLACE, SUBTRACT, box_mode, click_mode, combine, next_pick

_LOG = "Cable Explode"
_DRAG_PIXELS = 4


class Hit:
    """One feature under the cursor. ``geometry`` is the drawn line in map coordinates."""

    def __init__(self, layer, feature, geometry, distance, moved):
        self.layer = layer
        self.feature = feature
        self.geometry = geometry
        self.distance = distance
        self.moved = moved

    @property
    def key(self):
        return (self.layer.id(), int(self.feature.id()))


def find_hits(canvas, sources, area, point=None):
    """Features whose drawn line touches ``area`` (a map-CRS geometry), nearest to ``point`` first.

    ``sources`` yields ``(layer, display_geometries_by_fid, pad_in_layer_units)``.
    Hidden layers, layers outside their scale range, and features the renderer
    would not draw are skipped, as the QGIS tools do.
    """
    settings = canvas.mapSettings()
    visible = {layer.id() for layer in canvas.layers()}
    map_crs = settings.destinationCrs()
    project = QgsProject.instance()
    box = area.boundingBox()
    target = QgsGeometry.fromPointXY(point) if point is not None else None
    hits = []
    for layer, geometries, pad in sources:
        if layer.id() not in visible or not layer.isInScaleRange(canvas.scale()):
            continue
        try:
            to_map = None if layer.crs() == map_crs else QgsCoordinateTransform(layer.crs(), map_crs, project)
            search = QgsRectangle(settings.mapToLayerCoordinates(layer, box))
            # A drawn line can sit up to ``pad`` away from the stored line the index knows about.
            search.grow(max(pad, 0.0))
            request = QgsFeatureRequest().setFilterRect(search)
            with _RendererCheck(layer, settings) as check:
                for feature in layer.getFeatures(request):
                    stored = geometries.get(int(feature.id()))
                    geometry = QgsGeometry(stored if stored is not None else feature.geometry())
                    if geometry.isNull() or geometry.isEmpty() or not check.draws(feature):
                        continue
                    if to_map is not None:
                        geometry.transform(to_map)
                    if not geometry.intersects(area):
                        continue
                    distance = geometry.distance(target) if target is not None else 0.0
                    hits.append(Hit(layer, feature, geometry, distance, stored is not None))
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Warning)
    hits.sort(key=lambda hit: (hit.distance, hit.layer.name().casefold(), int(hit.feature.id())))
    return hits


class _RendererCheck:
    """Asks the layer's renderer whether it draws a feature, like the QGIS identify tool."""

    def __init__(self, layer, settings):
        self.layer = layer
        self.settings = settings
        self.renderer = None
        self.context = None

    def __enter__(self):
        renderer = self.layer.renderer()
        if renderer is None:
            return self
        try:
            self.context = QgsRenderContext.fromMapSettings(self.settings)
            self.context.setExpressionContext(QgsExpressionContext(
                QgsExpressionContextUtils.globalProjectLayerScopes(self.layer)))
            self.renderer = renderer.clone()
            self.renderer.startRender(self.context, self.layer.fields())
        except Exception:
            self.renderer = None
        return self

    def draws(self, feature):
        if self.renderer is None:
            return True
        try:
            self.context.expressionContext().setFeature(feature)
            return bool(self.renderer.willRenderFeature(feature, self.context))
        except Exception:
            return True

    def __exit__(self, *_exc):
        if self.renderer is not None:
            try:
                self.renderer.stopRender(self.context)
            except Exception:
                pass
        return False


def feature_title(layer, feature):
    """The layer's display expression, falling back to the cable id and then the feature id."""
    try:
        expression = QgsExpression(layer.displayExpression())
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        context.setFeature(feature)
        value = expression.evaluate(context)
        if value not in (None, NULL) and str(value).strip():
            return str(value)
    except Exception:
        pass
    for field in layer.fields():
        if field.name().casefold() == "cable_id":
            value = feature.attribute(field.name())
            if value not in (None, NULL) and str(value).strip():
                return str(value)
    return f"Feature {feature.id()}"


class _ExplodedTool(QgsMapTool):
    def __init__(self, canvas, plugin):
        super().__init__(canvas)
        self.plugin = plugin

    def sources(self):
        return self.plugin.pick_sources()

    def click_area(self, event):
        point = self.toMapCoordinates(event.pos())
        radius = QgsMapTool.searchRadiusMU(self.canvas())
        rect = QgsRectangle(point.x() - radius, point.y() - radius, point.x() + radius, point.y() + radius)
        return QgsGeometry.fromRect(rect), QgsPointXY(point)

    def status(self, text):
        try:
            self.plugin.iface.statusBarIface().showMessage(text, 6000)
        except Exception:
            pass


class ExplodedSelectTool(_ExplodedTool):
    """Click or drag to select cables where they are drawn.

    Shift or Ctrl click toggles one cable. Box: Shift adds, Ctrl removes,
    Ctrl+Shift keeps the overlap. Clicking the same stack again selects the
    next cable in it.
    """

    def __init__(self, canvas, plugin):
        super().__init__(canvas, plugin)
        self._press = None
        self._dragging = False
        self._band = None
        self._last_order = []
        self._last_pick = None
        self.setCursor(Qt.ArrowCursor)

    def deactivate(self):
        self._clear_band()
        self._press = None
        self._dragging = False
        super().deactivate()

    def canvasPressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._press = QPoint(event.pos())
        self._dragging = False

    def canvasMoveEvent(self, event):
        if self._press is None:
            return
        if not self._dragging and (event.pos() - self._press).manhattanLength() < _DRAG_PIXELS:
            return
        self._dragging = True
        if self._band is None:
            self._band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
            self._band.setColor(QColor(255, 0, 0, 60))
            self._band.setStrokeColor(QColor(255, 0, 0, 180))
            self._band.setWidth(1)
        self._band.setToGeometry(self._screen_box(self._press, event.pos()), None)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._press is None:
            return
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        ctrl = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))
        try:
            if self._dragging:
                self._select_box(self._screen_box(self._press, event.pos()), box_mode(shift, ctrl))
            else:
                self._select_click(event, click_mode(shift, ctrl))
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Critical)
            self.plugin.iface.messageBar().pushCritical(_LOG, "Could not select at that spot. See the log for details.")
        finally:
            self._clear_band()
            self._press = None
            self._dragging = False

    def _screen_box(self, start, end):
        # Four corners, so a rotated map still selects what is inside the drawn box.
        rect = QRect(start, end).normalized()
        corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        return QgsGeometry.fromPolygonXY([[QgsPointXY(self.toMapCoordinates(corner)) for corner in corners]])

    def _clear_band(self):
        if self._band is not None:
            self.canvas().scene().removeItem(self._band)
            self._band = None

    def _select_click(self, event, mode):
        area, point = self.click_area(event)
        hits = find_hits(self.canvas(), self.sources(), area, point)
        by_key = {hit.key: hit for hit in hits}
        if not hits:
            self._last_order, self._last_pick = [], None
            if mode == REPLACE:
                for layer, _geometries, _pad in self.sources():
                    layer.removeSelection()
            return
        if mode == REPLACE:
            pick, self._last_order = next_pick([hit.key for hit in hits], self._last_order, self._last_pick)
        else:
            pick, self._last_order = hits[0].key, []
        self._last_pick = pick
        chosen = by_key[pick]
        for layer, _geometries, _pad in self.sources():
            ids = [chosen.key[1]] if layer.id() == chosen.layer.id() else []
            if mode == REPLACE:
                layer.selectByIds(ids)
            elif ids:
                layer.selectByIds(sorted(combine(layer.selectedFeatureIds(), ids, mode)))
        title = feature_title(chosen.layer, chosen.feature)
        if mode == REPLACE and len(hits) > 1:
            position = self._last_order.index(pick) + 1
            self.status(f"Selected {title} ({chosen.layer.name()}), cable {position} of {len(hits)} here. "
                        "Click again for the next one.")
        else:
            self.status(f"{'Selected' if mode == REPLACE else 'Toggled'} {title} ({chosen.layer.name()}).")

    def _select_box(self, area, mode):
        self._last_order, self._last_pick = [], None
        hits = find_hits(self.canvas(), self.sources(), area)
        by_layer = {}
        for hit in hits:
            by_layer.setdefault(hit.layer.id(), set()).add(hit.key[1])
        total = 0
        for layer, _geometries, _pad in self.sources():
            ids = by_layer.get(layer.id(), set())
            if not ids and mode in (ADD, SUBTRACT):
                continue
            selected = combine(layer.selectedFeatureIds(), ids, mode)
            layer.selectByIds(sorted(selected))
            total += len(selected)
        self.status(f"{total} cable(s) selected.")


class ExplodedIdentifyTool(_ExplodedTool):
    """Click to list every cable drawn at that spot in the identify panel."""

    def __init__(self, canvas, plugin, panel):
        super().__init__(canvas, plugin)
        self.panel = panel
        self.setCursor(Qt.WhatsThisCursor)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        try:
            area, point = self.click_area(event)
            hits = find_hits(self.canvas(), self.sources(), area, point)
            self.panel.show_hits(hits)
            self.status(f"{len(hits)} cable(s) identified." if hits else "No cable is drawn at that spot.")
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Critical)
            self.plugin.iface.messageBar().pushCritical(_LOG, "Could not identify at that spot. See the log for details.")

    def deactivate(self):
        self.panel.clear_highlight()
        super().deactivate()


class IdentifyPanel(QDockWidget):
    """Results for the exploded identify tool: one entry per cable, with its attributes."""

    def __init__(self, iface, parent=None):
        super().__init__("Exploded Identify", parent)
        self.setObjectName("CableExplodeIdentify")
        self.iface = iface
        self._hits = []
        self._band = None
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(4, 4, 4, 4)
        self.summary = QLabel("Click a cable with Exploded identify.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Feature", "Value"])
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(lambda *_args: self._current_changed())
        self.tree.itemDoubleClicked.connect(lambda *_args: self._open_form())
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        self.select_button = QPushButton("Select")
        self.select_button.setToolTip("Make this cable the only selected feature on its layer.")
        self.zoom_button = QPushButton("Zoom to")
        self.form_button = QPushButton("Open form")
        self.form_button.setToolTip("Open the attribute form. It is editable when the layer is in edit mode.")
        for button, slot in ((self.select_button, self._select), (self.zoom_button, self._zoom),
                             (self.form_button, self._open_form)):
            button.clicked.connect(lambda *_args, slot=slot: self._safely(slot))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setWidget(body)
        self._update_buttons()

    def show_hits(self, hits):
        self._hits = list(hits)
        self.tree.clear()
        for index, hit in enumerate(self._hits):
            layer, feature = hit.layer, hit.feature
            top = QTreeWidgetItem([feature_title(layer, feature), layer.name()])
            top.setData(0, Qt.UserRole, index)
            top.setToolTip(0, f"{layer.name()} · feature {feature.id()}")
            fields = layer.fields()
            for field_index in range(fields.count()):
                value = feature.attribute(field_index)
                text = "NULL" if value in (None, NULL) else str(value)
                child = QTreeWidgetItem([layer.attributeDisplayName(field_index), text])
                child.setData(0, Qt.UserRole, index)
                top.addChild(child)
            derived = QTreeWidgetItem(["(Derived)", ""])
            derived.setData(0, Qt.UserRole, index)
            for name, value in (("Feature id", str(feature.id())),
                                ("Stored length", _length_text(layer, feature.geometry())),
                                ("Drawn offset", "Yes, shown apart from its stack" if hit.moved else "No")):
                child = QTreeWidgetItem([name, value])
                child.setData(0, Qt.UserRole, index)
                derived.addChild(child)
            top.addChild(derived)
            self.tree.addTopLevelItem(top)
        if self._hits:
            first = self.tree.topLevelItem(0)
            first.setExpanded(True)
            self.tree.setCurrentItem(first)
            self.tree.resizeColumnToContents(0)
            self.summary.setText(f"{len(self._hits)} cable(s) at that spot, nearest first. "
                                 "Choose one to highlight it on the map.")
        else:
            self.clear_highlight()
            self.summary.setText("No cable is drawn at that spot.")
        self._update_buttons()
        self.show()
        self.raise_()

    def current_hit(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        index = item.data(0, Qt.UserRole)
        return self._hits[index] if isinstance(index, int) and 0 <= index < len(self._hits) else None

    def clear_highlight(self):
        if self._band is not None:
            try:
                self.iface.mapCanvas().scene().removeItem(self._band)
            except Exception:
                pass
            self._band = None

    def _current_changed(self):
        self.clear_highlight()
        hit = self.current_hit()
        if hit is not None:
            self._band = QgsRubberBand(self.iface.mapCanvas(), QgsWkbTypes.LineGeometry)
            self._band.setColor(QColor(255, 0, 0, 200))
            self._band.setWidth(4)
            self._band.setToGeometry(hit.geometry, None)
        self._update_buttons()

    def _update_buttons(self):
        enabled = self.current_hit() is not None
        for button in (self.select_button, self.zoom_button, self.form_button):
            button.setEnabled(enabled)

    def _safely(self, slot):
        try:
            slot()
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Critical)
            self.iface.messageBar().pushCritical(_LOG, "That action failed. See the log for details.")

    def _live(self, hit):
        # The layer may have been removed since the click.
        try:
            return hit is not None and QgsProject.instance().mapLayer(hit.layer.id()) is hit.layer
        except RuntimeError:
            return False

    def reset(self, layer_ids=None):
        """Drop results for removed layers, or all results when ``layer_ids`` is None."""
        if layer_ids is None or any(self._layer_id(hit) in set(layer_ids) for hit in self._hits):
            self.clear_highlight()
            self._hits = []
            self.tree.clear()
            self.summary.setText("Click a cable with Exploded identify.")
            self._update_buttons()

    @staticmethod
    def _layer_id(hit):
        try:
            return hit.layer.id()
        except RuntimeError:
            return None

    def _select(self):
        hit = self.current_hit()
        if self._live(hit):
            hit.layer.selectByIds([hit.key[1]])

    def _zoom(self):
        hit = self.current_hit()
        if not self._live(hit):
            return
        canvas = self.iface.mapCanvas()
        extent = QgsRectangle(hit.geometry.boundingBox())
        if extent.isEmpty():
            extent.grow(max(canvas.mapUnitsPerPixel() * 50, 1e-9))
        else:
            extent.scale(1.2)
        canvas.setExtent(extent)
        canvas.refresh()

    def _open_form(self):
        hit = self.current_hit()
        if not self._live(hit):
            return
        feature = next(hit.layer.getFeatures(QgsFeatureRequest(hit.key[1])), None)
        if feature is None:
            self.iface.messageBar().pushWarning(_LOG, "That cable no longer exists on its layer.")
            return
        self.iface.openFeatureForm(hit.layer, feature, False, False)

    def closeEvent(self, event):
        self.clear_highlight()
        super().closeEvent(event)


def _length_text(layer, geometry):
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return ""
    try:
        measure = QgsDistanceArea()
        measure.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
        ellipsoid = QgsProject.instance().ellipsoid()
        if ellipsoid and ellipsoid.upper() != "NONE":
            measure.setEllipsoid(ellipsoid)
        meters = measure.convertLengthMeasurement(measure.measureLength(geometry), QgsUnitTypes.DistanceMeters)
        return f"{meters:.2f} m"
    except Exception:
        return f"{geometry.length():.2f} (layer units)"
