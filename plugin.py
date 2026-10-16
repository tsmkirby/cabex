"""Draw overlapping cables apart as a view aid. Stored geometry is never edited."""

from __future__ import annotations

import math
import traceback

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)
from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRenderer,
    QgsGeometry,
    QgsMessageLog,
    QgsPointXY,
    QgsProject,
    QgsReadWriteContext,
    QgsRectangle,
    QgsSettings,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .core.offset import CableLine, displace

_GAP_KEY = "cable_explode/gap_m"
_MIN_OVERLAP_KEY = "cable_explode/min_overlap_m"
_LAYERS_KEY = "cable_explode/layer_ids"
_CONFIGURED_KEY = "cable_explode/configured"
_DEFAULT_GAP = 0.2
_DEFAULT_MIN_OVERLAP = 1.0
_CABLE_FIELDS = frozenset({"cable_id", "strand_count", "fiber_count", "media_type"})
_LOG = "Cable Explode"


class ExplodeRenderer(QgsFeatureRenderer):
    """Draws the layer's real symbols on a display geometry.

    ``save`` writes the wrapped renderer, so a project file keeps the normal style.
    """

    TYPE = "cableExplode"

    def __init__(self, source, geometries=None, extent_pad=0.0):
        super().__init__(self.TYPE)
        self.source = source
        self.geometries = {} if geometries is None else geometries
        self.extent_pad = extent_pad

    def clone(self):
        source = self.source.clone() if self.source is not None else None
        cloned = ExplodeRenderer(source, self.geometries, self.extent_pad)
        self.copyRendererData(cloned)
        return cloned

    def save(self, doc, context):
        if self.source is not None:
            return self.source.save(doc, context)
        return super().save(doc, context)

    def startRender(self, context, fields):
        if self.source is not None:
            self.source.startRender(context, fields)

    def stopRender(self, context):
        if self.source is not None:
            self.source.stopRender(context)

    def renderFeature(self, feature, context, layer=-1, selected=False, drawVertexMarker=False):
        if self.source is None:
            return False
        geometry = self.geometries.get(int(feature.id()))
        if geometry is None:
            return self.source.renderFeature(feature, context, layer, selected, drawVertexMarker)
        drawn = QgsFeature(feature)
        drawn.setGeometry(QgsGeometry(geometry))
        return self.source.renderFeature(drawn, context, layer, selected, drawVertexMarker)

    def usedAttributes(self, context):
        if self.source is None:
            return set()
        return self.source.usedAttributes(context)

    def filter(self, fields=None):
        if self.source is None:
            return ""
        if fields is None:
            return self.source.filter()
        return self.source.filter(fields)

    def capabilities(self):
        if self.source is None:
            return super().capabilities()
        return self.source.capabilities()

    def flags(self):
        if self.source is None:
            return super().flags()
        return self.source.flags()

    def symbolForFeature(self, feature, context):
        if self.source is None:
            return None
        return self.source.symbolForFeature(feature, context)

    def originalSymbolForFeature(self, feature, context):
        if self.source is None:
            return None
        return self.source.originalSymbolForFeature(feature, context)

    def symbolsForFeature(self, feature, context):
        if self.source is None:
            return []
        return self.source.symbolsForFeature(feature, context)

    def originalSymbolsForFeature(self, feature, context):
        if self.source is None:
            return []
        return self.source.originalSymbolsForFeature(feature, context)

    def willRenderFeature(self, feature, context):
        if self.source is None:
            return False
        return self.source.willRenderFeature(feature, context)

    def symbols(self, context):
        if self.source is None:
            return []
        return self.source.symbols(context)

    def legendSymbolItems(self):
        if self.source is None:
            return []
        return self.source.legendSymbolItems()

    def legendKeysForFeature(self, feature, context):
        if self.source is None:
            return set()
        return self.source.legendKeysForFeature(feature, context)

    def legendKeys(self):
        if self.source is None:
            return set()
        return self.source.legendKeys()

    def legendSymbolItemsCheckable(self):
        if self.source is None:
            return False
        return self.source.legendSymbolItemsCheckable()

    def legendSymbolItemChecked(self, key):
        if self.source is None:
            return True
        return self.source.legendSymbolItemChecked(key)

    def checkLegendSymbolItem(self, key, state=True):
        if self.source is not None:
            self.source.checkLegendSymbolItem(key, state)

    def setLegendSymbolItem(self, key, symbol):
        if self.source is not None:
            self.source.setLegendSymbolItem(key, symbol)

    def legendClassificationAttribute(self):
        if self.source is None:
            return ""
        return self.source.legendClassificationAttribute()

    def filterNeedsGeometry(self):
        if self.source is None:
            return False
        return self.source.filterNeedsGeometry()

    def canSkipRender(self):
        if self.source is None:
            return False
        return self.source.canSkipRender()

    def usesEmbeddedSymbols(self):
        if self.source is None:
            return False
        return self.source.usesEmbeddedSymbols()

    def accept(self, visitor):
        if self.source is None:
            return True
        return self.source.accept(visitor)

    def modifyRequestExtent(self, extent, context):
        if self.source is not None:
            self.source.modifyRequestExtent(extent, context)
        if self.extent_pad > 0:
            extent.grow(self.extent_pad)


class SettingsDialog(QDialog):
    def __init__(self, layers, selected_ids, gap, min_overlap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Explode settings")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Only segments that lie on top of each other are drawn apart, and only "
            "when they share at least the minimum length. Neighbors in that stack "
            "are separated by the gap. Everything else stays on the real geometry. "
            "Nothing is written to the layer."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        note = QLabel("Cables count as stacked when they are within 0.1 m of the same path.")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel("Separation"))
        self._gap = QDoubleSpinBox()
        self._gap.setRange(0.01, 5.0)
        self._gap.setDecimals(2)
        self._gap.setSingleStep(0.05)
        self._gap.setValue(gap)
        self._gap.setSuffix(" m")
        row.addWidget(self._gap)
        layout.addLayout(row)
        overlap_row = QHBoxLayout()
        overlap_row.addWidget(QLabel("Minimum shared length"))
        self._min_overlap = QDoubleSpinBox()
        self._min_overlap.setRange(0.0, 500.0)
        self._min_overlap.setDecimals(1)
        self._min_overlap.setSingleStep(1.0)
        self._min_overlap.setValue(min_overlap)
        self._min_overlap.setSuffix(" m")
        overlap_row.addWidget(self._min_overlap)
        layout.addLayout(overlap_row)
        layout.addWidget(QLabel("Line layers"))
        self._layers = QListWidget()
        selected = set(selected_ids)
        for layer in layers:
            label = layer.name()
            if _is_cable_layer(layer):
                label = f"{label} (cable)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if layer.id() in selected else Qt.Unchecked)
            self._layers.addItem(item)
        layout.addWidget(self._layers)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def gap(self):
        return float(self._gap.value())

    def min_overlap(self):
        return float(self._min_overlap.value())

    def selected_ids(self):
        chosen = []
        for index in range(self._layers.count()):
            item = self._layers.item(index)
            if item.checkState() == Qt.Checked:
                chosen.append(item.data(Qt.UserRole))
        return chosen


class _LayerView:
    def __init__(self, original, wrapper):
        self.original = original
        self.wrapper = wrapper


class CableExplodePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self._actions = []
        self._toggle = None
        self._timer = None
        self._states = {}
        self._watches = {}
        self._updating = False

    def initGui(self):
        self.toolbar = self.iface.addToolBar("Cable Explode")
        self.toolbar.setObjectName("CableExplode")
        self._toggle = QAction("Explode cables", self.iface.mainWindow())
        self._toggle.setCheckable(True)
        self._toggle.setToolTip("Draw stacked cables apart. Does not change geometry.")
        self._toggle.toggled.connect(self._toggled)
        settings_action = QAction("Explode settings", self.iface.mainWindow())
        settings_action.setToolTip("Set the separation, minimum shared length, and layers.")
        settings_action.triggered.connect(self._open_settings)
        self.toolbar.addAction(self._toggle)
        self.toolbar.addAction(settings_action)
        self._actions = [self._toggle, settings_action]
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._rebuild)
        project = QgsProject.instance()
        project.writeProject.connect(self._on_write_project)
        project.writeMapLayer.connect(self._on_write_map_layer)
        project.cleared.connect(self._on_cleared)
        project.layersWillBeRemoved.connect(self._on_layers_removed)
        project.crsChanged.connect(self._schedule)

    def unload(self):
        self._disable()
        project = QgsProject.instance()
        for signal, slot in (
            (project.writeProject, self._on_write_project),
            (project.writeMapLayer, self._on_write_map_layer),
            (project.cleared, self._on_cleared),
            (project.layersWillBeRemoved, self._on_layers_removed),
            (project.crsChanged, self._schedule),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                pass
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        for action in self._actions:
            if self.toolbar is not None:
                self.toolbar.removeAction(action)
        self._actions = []
        if self.toolbar is not None:
            del self.toolbar
            self.toolbar = None

    def _toggled(self, checked):
        if checked:
            if not self._enable():
                self._set_checked(False)
        else:
            self._disable()

    def _open_settings(self):
        layers = self._line_layers()
        dialog = SettingsDialog(
            layers,
            [layer.id() for layer in self._participating(layers)],
            self._gap(),
            self._min_overlap(),
            self.iface.mainWindow(),
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        settings = QgsSettings()
        settings.setValue(_GAP_KEY, dialog.gap())
        settings.setValue(_MIN_OVERLAP_KEY, dialog.min_overlap())
        settings.setValue(_LAYERS_KEY, dialog.selected_ids())
        settings.setValue(_CONFIGURED_KEY, True)
        if self._toggle.isChecked():
            self._sync_membership()
            if not self._rebuild():
                self._set_checked(False)

    def _enable(self):
        layers = self._participating()
        if not layers:
            self.iface.messageBar().pushWarning(
                _LOG,
                "No cable line layers are selected. Open Explode settings to choose layers.",
            )
            return False
        for layer in layers:
            self._watch(layer)
        if not self._rebuild():
            return False
        self.iface.messageBar().pushInfo(
            _LOG,
            "Stacked cables are drawn apart. Geometries are not changed.",
        )
        return True

    def _disable(self):
        if self._timer is not None:
            self._timer.stop()
        project = QgsProject.instance()
        was_dirty = project.isDirty()
        for layer_id in list(self._states):
            self._restore(layer_id)
        for layer_id in list(self._watches):
            self._unwatch(project.mapLayer(layer_id))
        if not was_dirty:
            project.setDirty(False)
        canvas = self.iface.mapCanvas()
        if canvas is not None:
            canvas.refresh()

    def _rebuild(self):
        if self._toggle is None or not self._toggle.isChecked():
            return True
        self._updating = True
        try:
            self._apply()
        except Exception:
            self._updating = False
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Critical)
            self.iface.messageBar().pushCritical(_LOG, "Could not separate the cable layers.")
            self._set_checked(False)
            self._disable()
            return False
        self._updating = False
        return True

    def _apply(self):
        layers = self._participating()
        for layer in layers:
            self._watch(layer)
        by_layer, meter_crs, pad_meters = _display_geometries(layers, self._gap(), self._min_overlap())
        project = QgsProject.instance()
        was_dirty = project.isDirty()
        installed = False
        for layer in layers:
            geometries = by_layer.get(layer.id(), {})
            pad = _pad_in_layer_units(layer, meter_crs, pad_meters)
            state = self._states.get(layer.id())
            renderer = layer.renderer()
            if state is not None and renderer is not None and renderer.type() == ExplodeRenderer.TYPE:
                state.wrapper.geometries = geometries
                state.wrapper.extent_pad = pad
                layer.triggerRepaint()
                continue
            if renderer is None:
                continue
            original = renderer.clone()
            wrapper = ExplodeRenderer(renderer.clone(), geometries, pad)
            renderer.copyRendererData(wrapper)
            self._states[layer.id()] = _LayerView(original, wrapper)
            layer.setRenderer(wrapper)
            layer.triggerRepaint()
            installed = True
        if installed and not was_dirty:
            project.setDirty(False)
        canvas = self.iface.mapCanvas()
        if canvas is not None:
            canvas.refresh()

    def _sync_membership(self):
        wanted = {layer.id() for layer in self._participating()}
        for layer_id in list(self._states):
            if layer_id not in wanted:
                self._restore(layer_id)
                self._unwatch(QgsProject.instance().mapLayer(layer_id))

    def _restore(self, layer_id):
        state = self._states.pop(layer_id, None)
        layer = QgsProject.instance().mapLayer(layer_id)
        if state is None or not isinstance(layer, QgsVectorLayer):
            return
        renderer = layer.renderer()
        if renderer is not None and renderer.type() == ExplodeRenderer.TYPE:
            self._updating = True
            try:
                layer.setRenderer(state.original.clone())
                layer.triggerRepaint()
            finally:
                self._updating = False

    def _schedule(self, *args):
        if self._updating or self._toggle is None or not self._toggle.isChecked() or self._timer is None:
            return
        self._timer.start(100)

    def _on_style(self, layer_id):
        if self._updating:
            return
        state = self._states.get(layer_id)
        if state is None:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            return
        renderer = layer.renderer()
        if renderer is None or renderer.type() == ExplodeRenderer.TYPE:
            return
        state.original = renderer.clone()
        state.wrapper.source = renderer.clone()
        renderer.copyRendererData(state.wrapper)
        self._updating = True
        try:
            layer.setRenderer(state.wrapper)
            layer.triggerRepaint()
        finally:
            self._updating = False

    def _watch(self, layer):
        layer_id = layer.id()
        if layer_id in self._watches:
            return
        style_slot = lambda layer_id=layer_id: self._on_style(layer_id)
        bindings = (
            (layer.geometryChanged, self._schedule),
            (layer.featureAdded, self._schedule),
            (layer.featureDeleted, self._schedule),
            (layer.dataChanged, self._schedule),
            (layer.crsChanged, self._schedule),
            (layer.styleChanged, style_slot),
        )
        for signal, slot in bindings:
            signal.connect(slot)
        self._watches[layer_id] = bindings

    def _unwatch(self, layer):
        if layer is None:
            return
        bindings = self._watches.pop(layer.id(), None)
        if not bindings:
            return
        for signal, slot in bindings:
            try:
                signal.disconnect(slot)
            except TypeError:
                pass

    def _on_layers_removed(self, layer_ids):
        project = QgsProject.instance()
        for layer_id in layer_ids:
            layer = project.mapLayer(layer_id)
            self._restore(layer_id)
            self._unwatch(layer)

    def _on_cleared(self):
        if self._timer is not None:
            self._timer.stop()
        self._states.clear()
        self._watches.clear()
        self._set_checked(False)

    def _on_write_project(self, doc):
        try:
            if not self._states:
                return
            nodes = doc.documentElement().elementsByTagName("maplayer")
            for index in range(nodes.count()):
                element = nodes.at(index).toElement()
                layer_id = element.firstChildElement("id").text()
                state = self._states.get(layer_id)
                if state is not None:
                    _replace_exploded_renderer(element, doc, state.original)
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Warning)

    def _on_write_map_layer(self, layer, element, doc):
        try:
            if layer is None:
                return
            state = self._states.get(layer.id())
            if state is None:
                return
            _replace_exploded_renderer(element, doc, state.original)
        except Exception:
            QgsMessageLog.logMessage(traceback.format_exc(), _LOG, Qgis.Warning)

    def _set_checked(self, checked):
        if self._toggle is None:
            return
        self._toggle.blockSignals(True)
        self._toggle.setChecked(checked)
        self._toggle.blockSignals(False)

    def _gap(self):
        try:
            gap = float(QgsSettings().value(_GAP_KEY, _DEFAULT_GAP))
        except (TypeError, ValueError):
            gap = _DEFAULT_GAP
        if gap <= 0:
            return _DEFAULT_GAP
        return gap

    def _min_overlap(self):
        try:
            length = float(QgsSettings().value(_MIN_OVERLAP_KEY, _DEFAULT_MIN_OVERLAP))
        except (TypeError, ValueError):
            length = _DEFAULT_MIN_OVERLAP
        if length < 0:
            return _DEFAULT_MIN_OVERLAP
        return length

    def _line_layers(self):
        layers = []
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.LineGeometry:
                layers.append(layer)
        layers.sort(key=lambda layer: (not _is_cable_layer(layer), layer.name().casefold()))
        return layers

    def _participating(self, layers=None):
        if layers is None:
            layers = self._line_layers()
        settings = QgsSettings()
        if settings.value(_CONFIGURED_KEY, False, type=bool):
            selected = set(_as_list(settings.value(_LAYERS_KEY, [])))
            return [layer for layer in layers if layer.id() in selected]
        return [layer for layer in layers if _is_cable_layer(layer)]


def _is_cable_layer(layer):
    if not isinstance(layer, QgsVectorLayer):
        return False
    if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.LineGeometry:
        return False
    names = {field.name().casefold() for field in layer.fields()}
    return bool(names & _CABLE_FIELDS)


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _replace_exploded_renderer(element, doc, renderer):
    if element is None or element.isNull():
        return
    host = element
    if element.tagName() == "renderer-v2":
        host = element.parentNode().toElement()
    current = host.firstChildElement("renderer-v2")
    if current.isNull() or current.attribute("type") != ExplodeRenderer.TYPE:
        return
    saved = renderer.save(doc, QgsReadWriteContext())
    if saved is None or saved.isNull():
        return
    host.replaceChild(saved, current)


def _shift_pad(lines, displaced):
    pad = 0.0
    for line in lines:
        updated = displaced.get(line.key)
        if not updated:
            continue
        for x, y in updated:
            pad = max(pad, _distance_to_path(x, y, line.coords))
    return pad


def _distance_to_path(x, y, coords):
    best = 0.0
    found = False
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        dx = x1 - x0
        dy = y1 - y0
        length_sq = dx * dx + dy * dy
        if length_sq == 0.0:
            dist = math.hypot(x - x0, y - y0)
        else:
            ratio = ((x - x0) * dx + (y - y0) * dy) / length_sq
            if ratio < 0.0:
                ratio = 0.0
            elif ratio > 1.0:
                ratio = 1.0
            dist = math.hypot(x - (x0 + ratio * dx), y - (y0 + ratio * dy))
        if not found or dist < best:
            best = dist
            found = True
    return best


def _display_geometries(layers, gap, min_overlap):
    meter_crs = _meter_crs(layers)
    if not meter_crs.isValid():
        raise RuntimeError("Could not build a meter CRS for the cable offset.")
    project = QgsProject.instance()
    collected = []
    lines = []
    for layer in layers:
        if not layer.crs().isValid():
            raise RuntimeError(f"{layer.name()} has no CRS, so a meter offset cannot be calculated.")
        to_meter = None
        if layer.crs() != meter_crs:
            to_meter = QgsCoordinateTransform(layer.crs(), meter_crs, project)
        for feature in layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            parts = _parts(geometry)
            if not parts:
                continue
            meter_parts = parts
            if to_meter is not None:
                meter_geometry = QgsGeometry(geometry)
                meter_geometry.transform(to_meter)
                meter_parts = _parts(meter_geometry)
            cable_id = _cable_identifier(feature, layer)
            multipart = geometry.isMultipart() or QgsWkbTypes.isMultiType(layer.wkbType())
            for part_index, part in enumerate(parts):
                source = meter_parts[part_index] if part_index < len(meter_parts) else []
                coords = tuple((point.x(), point.y()) for point in source)
                record = {
                    "layer_id": layer.id(),
                    "fid": int(feature.id()),
                    "part_index": part_index,
                    "multipart": multipart,
                    "original": part,
                    "key": None,
                    "meter_coords": (),
                }
                if len(coords) >= 2:
                    cable = CableLine((layer.id(), int(feature.id()), part_index), cable_id, coords)
                    record["key"] = cable.key
                    record["meter_coords"] = cable.coords
                    lines.append(cable)
                collected.append(record)
    displaced = displace(lines, gap, min_overlap) if lines else {}
    pad = _shift_pad(lines, displaced)
    grouped = {}
    for record in collected:
        grouped.setdefault((record["layer_id"], record["fid"]), []).append(record)
    by_layer = {layer.id(): {} for layer in layers}
    for (layer_id, fid), records in grouped.items():
        layer = project.mapLayer(layer_id)
        if not isinstance(layer, QgsVectorLayer):
            continue
        records.sort(key=lambda record: record["part_index"])
        geometry = _display_geometry(layer, records, displaced, meter_crs)
        if geometry is not None:
            by_layer[layer_id][fid] = geometry
    return by_layer, meter_crs, pad


def _display_geometry(layer, records, displaced, meter_crs):
    back = None
    if layer.crs() != meter_crs:
        back = QgsCoordinateTransform(meter_crs, layer.crs(), QgsProject.instance())
    polylines = []
    moved = False
    for record in records:
        updated = displaced.get(record["key"]) if record["key"] is not None else None
        if updated is not None and not _same_coords(updated, record["meter_coords"]):
            geometry = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in updated])
            if back is not None:
                geometry.transform(back)
            line = geometry.asPolyline()
            if line:
                polylines.append(line)
                moved = True
                continue
        polylines.append(record["original"])
    if not moved or not polylines:
        return None
    multipart = records[0]["multipart"] or len(polylines) > 1
    if multipart:
        return QgsGeometry.fromMultiPolylineXY(polylines)
    return QgsGeometry.fromPolylineXY(polylines[0])


def _meter_crs(layers):
    project = QgsProject.instance()
    project_crs = project.crs()
    if project_crs.isValid() and project_crs.mapUnits() == QgsUnitTypes.DistanceMeters:
        return project_crs
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    extent = None
    for layer in layers:
        layer_extent = QgsRectangle(layer.extent())
        if layer_extent.isNull() or layer_extent.isEmpty():
            continue
        source = layer.crs() if layer.crs().isValid() else project_crs
        if source.isValid() and source != wgs84:
            try:
                transformed = QgsCoordinateTransform(source, wgs84, project).transformBoundingBox(layer_extent)
            except Exception:
                continue
            layer_extent = transformed
        extent = layer_extent if extent is None else extent.combined(layer_extent)
    if extent is None or extent.isEmpty():
        lon, lat = 0.0, 0.0
    else:
        center = extent.center()
        lon, lat = center.x(), center.y()
    return QgsCoordinateReferenceSystem.fromProj(
        f"+proj=aeqd +lat_0={lat:.8f} +lon_0={lon:.8f} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
    )


def _pad_in_layer_units(layer, meter_crs, meters):
    if meters <= 0 or not layer.crs().isValid():
        return 0.0
    if layer.crs() == meter_crs or not meter_crs.isValid():
        return meters
    extent = layer.extent()
    if extent.isEmpty():
        return meters
    project = QgsProject.instance()
    try:
        center = extent.center()
        origin = QgsCoordinateTransform(layer.crs(), meter_crs, project).transform(center)
        east = QgsCoordinateTransform(meter_crs, layer.crs(), project).transform(QgsPointXY(origin.x() + meters, origin.y()))
    except Exception:
        return meters
    return math.hypot(east.x() - center.x(), east.y() - center.y())


def _parts(geometry):
    if geometry.isMultipart():
        return [part for part in geometry.asMultiPolyline() if part]
    line = geometry.asPolyline()
    return [line] if line else []


def _cable_identifier(feature, layer):
    for field in layer.fields():
        if field.name().casefold() != "cable_id":
            continue
        value = feature.attribute(field.name())
        if value in (None, NULL):
            break
        text = str(value).strip()
        if text and text.casefold() != "null":
            return text
        break
    return str(feature.id())


def _same_coords(updated, original):
    if len(updated) != len(original):
        return False
    return all(left == right for left, right in zip(updated, original))
