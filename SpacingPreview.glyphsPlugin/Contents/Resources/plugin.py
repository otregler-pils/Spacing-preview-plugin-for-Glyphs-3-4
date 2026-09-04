# encoding: utf-8

import objc

from AppKit import (
    NSMenuItem, NSWindow, NSView, NSColor, NSBezierPath, NSGraphicsContext, NSApplication,
    NSMakeRect, NSMakePoint, NSBackingStoreBuffered,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskUtilityWindow,
    NSFloatingWindowLevel, NSButton, NSBezelStyleRounded,
    NSViewWidthSizable, NSViewHeightSizable, NSViewMinYMargin, NSViewMaxXMargin,
    NSString, NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
)
from Foundation import NSObject, NSNotificationCenter
from GlyphsApp import Glyphs, UPDATEINTERFACE, EDIT_MENU, ONSTATE, OFFSTATE

# Optional Glyphs document callbacks. They exist in current Glyphs API and make the
# floating panel behave better when documents are activated/closed. Import them
# defensively so the same plugin keeps loading across Glyphs 3/4 builds.
try:
    from GlyphsApp import DOCUMENTWILLCLOSE
except Exception:
    DOCUMENTWILLCLOSE = None
try:
    from GlyphsApp import DOCUMENTDIDCLOSE
except Exception:
    DOCUMENTDIDCLOSE = None
try:
    from GlyphsApp import DOCUMENTACTIVATED
except Exception:
    DOCUMENTACTIVATED = None

from GlyphsApp.plugins import GeneralPlugin

PLUGIN_ID = "com.pilstype.SpacingPreview"
KEY_DARK_MODE = PLUGIN_ID + ".darkMode"
KEY_KERNING_ON = PLUGIN_ID + ".kerningOn"
KEY_STRING_INDEX = PLUGIN_ID + ".stringIndex"  # 0..n-1 = selected string

# Spacing proof strings. {g} is replaced with the glyph selected in the Edit View.
# 1: nnxooxHHxOO
# 2: nnxnoxoo
# 3: HHxHOxOO
# 4: nnnxnnn
# 5: HHHxHHH
TEMPLATE_LINES = [
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
]
WINDOW_WIDTH = 780
WINDOW_HEIGHT = 220
CONTROL_HEIGHT = 36
PADDING_X = 34
PADDING_Y = 22
LINE_GAP = 120


def _default_bool(key, fallback):
    """Read a boolean from Glyphs.defaults without using membership tests.

    Glyphs.defaults does not reliably support `key in Glyphs.defaults` in all
    Glyphs 3 builds, so avoid `in` and use indexed lookup with a fallback.
    """
    try:
        value = Glyphs.defaults[key]
    except Exception:
        try:
            value = Glyphs.defaults.get(key, fallback)
        except Exception:
            value = fallback
    if value is None:
        return bool(fallback)
    return bool(value)


def _default_int(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        try:
            value = Glyphs.defaults.get(key, fallback)
        except Exception:
            value = fallback
    if value is None:
        return int(fallback)
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _ensure_default_int(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        value = None
    if value is None:
        Glyphs.defaults[key] = int(fallback)


def _ensure_default_bool(key, fallback):
    try:
        value = Glyphs.defaults[key]
    except Exception:
        value = None
    if value is None:
        Glyphs.defaults[key] = bool(fallback)


def _has_active_edit_view():
    """True only when Glyphs has an active font Edit View tab."""
    font = Glyphs.font
    if font is None:
        return False
    try:
        return font.currentTab is not None
    except Exception:
        return False


def _selected_context():
    font = Glyphs.font
    if font is None or not font.selectedLayers:
        return None

    selected_layer = font.selectedLayers[0]
    selected_glyph = selected_layer.parent
    selected_name = selected_glyph.name

    try:
        master_id = selected_layer.associatedMasterId
    except Exception:
        master_id = font.selectedFontMaster.id

    master = font.selectedFontMaster
    return font, selected_layer, selected_name, master_id, master


def _kerning_value_for_pair(font, master_id, left_glyph, right_glyph):
    """Return the effective LTR kerning value for a glyph pair.

    Glyphs' kerningForPair() returns only the value for the exact keys supplied.
    To approximate what Glyphs applies in the UI, we try explicit glyph keys first,
    then one-sided group exceptions, then the group-to-group pair.
    """
    if left_glyph is None or right_glyph is None:
        return 0.0

    left_name = left_glyph.name
    right_name = right_glyph.name

    try:
        left_group_key = left_glyph.rightKerningKey
    except Exception:
        left_group_key = left_name

    try:
        right_group_key = right_glyph.leftKerningKey
    except Exception:
        right_group_key = right_name

    candidates = [
        (left_name, right_name),
        (left_name, right_group_key),
        (left_group_key, right_name),
        (left_group_key, right_group_key),
    ]

    seen = set()
    for left_key, right_key in candidates:
        if not left_key or not right_key:
            continue
        pair_key = (left_key, right_key)
        if pair_key in seen:
            continue
        seen.add(pair_key)
        try:
            value = font.kerningForPair(master_id, left_key, right_key)
        except Exception:
            value = None
        if value is not None:
            return float(value)

    return 0.0


class PilsSpacingDrawView(NSView):

    def initWithFrame_(self, frame):
        self = objc.super(PilsSpacingDrawView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.darkMode = _default_bool(KEY_DARK_MODE, True)
        self.kerningOn = _default_bool(KEY_KERNING_ON, True)
        self.stringIndex = _default_int(KEY_STRING_INDEX, 0)
        return self

    def isFlipped(self):
        return False

    def drawRect_(self, rect):
        bounds = self.bounds()
        width = bounds.size.width
        height = bounds.size.height

        bg = NSColor.blackColor() if self.darkMode else NSColor.whiteColor()
        fg = NSColor.whiteColor() if self.darkMode else NSColor.blackColor()

        bg.set()
        NSBezierPath.bezierPathWithRect_(bounds).fill()

        ctx = _selected_context()
        if ctx is None:
            self._draw_message_("Select a glyph in Edit View.", fg)
            return

        font, selected_layer, selected_name, master_id, master = ctx

        line_data = []
        missing = []
        max_line_width = 1.0

        index = int(self.stringIndex)
        if index < 0 or index >= len(TEMPLATE_LINES):
            index = 0
            self.stringIndex = 0
        selected_templates = [TEMPLATE_LINES[index]]

        for template in selected_templates:
            items = []

            for token in template:
                glyph_name = selected_name if token == "{g}" else token

                if glyph_name == selected_name:
                    glyph = selected_layer.parent
                    layer = selected_layer
                else:
                    glyph = font.glyphs[glyph_name]
                    if glyph is None:
                        missing.append(glyph_name)
                        layer = None
                    else:
                        layer = glyph.layers[master_id]

                if layer is not None:
                    advance = float(layer.width or 0)
                    if advance <= 0:
                        advance = 300.0
                    items.append({
                        "glyph_name": glyph_name,
                        "glyph": glyph,
                        "layer": layer,
                        "advance": advance,
                    })

            if items:
                # Precompute x positions in font units. Kerning is added after the left glyph.
                positions = []
                pen_x = 0.0
                for index, item in enumerate(items):
                    positions.append(pen_x)
                    pen_x += item["advance"]
                    if self.kerningOn and index < len(items) - 1:
                        pen_x += _kerning_value_for_pair(
                            font,
                            master_id,
                            item["glyph"],
                            items[index + 1]["glyph"],
                        )

                line_width = max(pen_x, 1.0)
                max_line_width = max(max_line_width, line_width)
                line_data.append({
                    "items": items,
                    "positions": positions,
                    "width": line_width,
                })

        if not line_data:
            self._draw_message_("No drawable layers found.", fg)
            return

        try:
            ascender = float(master.ascender)
            descender = float(master.descender)
        except Exception:
            ascender = 800.0
            descender = -200.0

        em_height = max(ascender - descender, 1.0)
        line_count = len(line_data)
        line_step = em_height + LINE_GAP
        block_height = em_height * line_count + LINE_GAP * max(line_count - 1, 0)

        usable_width = max(width - 2 * PADDING_X, 10.0)
        usable_height = max(height - 2 * PADDING_Y, 10.0)
        scale = min(usable_width / max_line_width, usable_height / block_height)

        block_bottom = (height - block_height * scale) / 2.0

        fg.set()
        for line_index, line in enumerate(line_data):
            visual_index = line_count - 1 - line_index
            line_origin_x = (width - line["width"] * scale) / 2.0
            baseline = block_bottom + (visual_index * line_step - descender) * scale

            for item, item_x in zip(line["items"], line["positions"]):
                path = item["layer"].completeBezierPath
                if path is not None:
                    NSGraphicsContext.saveGraphicsState()
                    transform = objc.lookUpClass("NSAffineTransform").transform()
                    transform.translateXBy_yBy_(line_origin_x + item_x * scale, baseline)
                    transform.scaleBy_(scale)
                    transform.concat()
                    fg.set()
                    path.fill()
                    NSGraphicsContext.restoreGraphicsState()

        if missing:
            self._draw_message_("Missing: " + ", ".join(sorted(set(missing))), fg, small=True)

    def _draw_message_(self, message, color, small=False):
        size = 10 if small else 13
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(size),
            NSForegroundColorAttributeName: color,
        }
        y = 8 if small else 20
        NSString.stringWithString_(message).drawAtPoint_withAttributes_(NSMakePoint(16, y), attrs)


class PilsSpacingPanelController(NSObject):

    def init(self):
        self = objc.super(PilsSpacingPanelController, self).init()
        if self is None:
            return None
        self.window = None
        self.drawView = None
        self.themeButton = None
        self.kerningButton = None
        self.stringButton = None
        self._callback = None
        self._glyphsCallbacks = []
        self._notificationsRegistered = False
        return self

    def open(self):
        if self.window is not None:
            self._sync_visibility()
            self.update_(None)
            return

        style = (
            NSWindowStyleMaskTitled |
            NSWindowStyleMaskClosable |
            NSWindowStyleMaskResizable |
            NSWindowStyleMaskUtilityWindow
        )
        rect = NSMakeRect(240, 240, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Spacing Preview")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setDelegate_(self)
        self._register_notifications()

        content = self.window.contentView()
        content_bounds = content.bounds()
        content_w = content_bounds.size.width
        content_h = content_bounds.size.height

        self.drawView = PilsSpacingDrawView.alloc().initWithFrame_(
            NSMakeRect(0, 0, content_w, content_h - CONTROL_HEIGHT)
        )
        self.drawView.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        content.addSubview_(self.drawView)

        self.themeButton = NSButton.alloc().initWithFrame_(
            NSMakeRect(12, content_h - 28, 110, 22)
        )
        self.themeButton.setTitle_("Theme: Black" if self.drawView.darkMode else "Theme: White")
        self.themeButton.setBezelStyle_(NSBezelStyleRounded)
        self.themeButton.setTarget_(self)
        self.themeButton.setAction_("toggleTheme:")
        self.themeButton.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(self.themeButton)

        self.kerningButton = NSButton.alloc().initWithFrame_(
            NSMakeRect(128, content_h - 28, 110, 22)
        )
        self.kerningButton.setTitle_("Kerning: On" if self.drawView.kerningOn else "Kerning: Off")
        self.kerningButton.setBezelStyle_(NSBezelStyleRounded)
        self.kerningButton.setTarget_(self)
        self.kerningButton.setAction_("toggleKerning:")
        self.kerningButton.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(self.kerningButton)

        self.stringButton = NSButton.alloc().initWithFrame_(
            NSMakeRect(244, content_h - 28, 120, 22)
        )
        self.stringButton.setTitle_(self._string_button_title())
        self.stringButton.setBezelStyle_(NSBezelStyleRounded)
        self.stringButton.setTarget_(self)
        self.stringButton.setAction_("cycleString:")
        self.stringButton.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
        content.addSubview_(self.stringButton)

        self._callback = self.update_
        Glyphs.addCallback(self._callback, UPDATEINTERFACE)

        # Glyphs 3.0.4+ / Glyphs 4 document callbacks. These are more reliable
        # than only observing NSWindow notifications when documents are closed or
        # activated. Keep them optional for older/minor builds.
        self._add_glyphs_callback(self.documentWillCloseCallback_, DOCUMENTWILLCLOSE)
        self._add_glyphs_callback(self.documentDidCloseCallback_, DOCUMENTDIDCLOSE)
        self._add_glyphs_callback(self.documentActivatedCallback_, DOCUMENTACTIVATED)

        self._sync_visibility(make_key=True)
        self.update_(None)

    def close(self):
        self._remove_callback()
        self._remove_notifications()
        if self.window is not None:
            window = self.window
            self.window = None
            self.drawView = None
            self.themeButton = None
            self.kerningButton = None
            self.stringButton = None
            window.close()

    def isOpen(self):
        return self.window is not None

    def _string_button_title(self):
        if self.drawView is None:
            return "String: 1"
        index = int(self.drawView.stringIndex)
        if index < 0 or index >= len(TEMPLATE_LINES):
            index = 0
            self.drawView.stringIndex = 0
        return "String: %d" % (index + 1)

    def _add_glyphs_callback(self, function, hook):
        if hook is None:
            return
        try:
            Glyphs.addCallback(function, hook)
            self._glyphsCallbacks.append(function)
        except Exception:
            pass

    def documentWillCloseCallback_(self, info):
        if self.window is not None and self.window.isVisible():
            self.window.orderOut_(None)

    def documentDidCloseCallback_(self, info):
        self._sync_visibility()
        self.update_(None)

    def documentActivatedCallback_(self, info):
        self._sync_visibility()
        self.update_(None)

    def _register_notifications(self):
        if self._notificationsRegistered:
            return
        center = NSNotificationCenter.defaultCenter()
        center.addObserver_selector_name_object_(
            self, "applicationDidBecomeActive:", "NSApplicationDidBecomeActiveNotification", None
        )
        center.addObserver_selector_name_object_(
            self, "applicationDidResignActive:", "NSApplicationDidResignActiveNotification", None
        )
        center.addObserver_selector_name_object_(
            self, "applicationWillTerminate:", "NSApplicationWillTerminateNotification", None
        )
        center.addObserver_selector_name_object_(
            self, "windowWillCloseNotification:", "NSWindowWillCloseNotification", None
        )
        center.addObserver_selector_name_object_(
            self, "windowDidBecomeKeyNotification:", "NSWindowDidBecomeKeyNotification", None
        )
        self._notificationsRegistered = True

    def _remove_notifications(self):
        if not self._notificationsRegistered:
            return
        try:
            NSNotificationCenter.defaultCenter().removeObserver_(self)
        except Exception:
            pass
        self._notificationsRegistered = False

    def _has_visible_host_window(self):
        """Return True when Glyphs has another visible window besides this panel.

        Without this guard, a floating utility window can keep Glyphs active after
        the font/edit window has been closed, and stale Glyphs.font/currentTab
        state may make the panel remain visible.
        """
        try:
            windows = NSApplication.sharedApplication().windows()
        except Exception:
            return True

        for window in windows:
            if self.window is not None and window == self.window:
                continue
            try:
                if not window.isVisible():
                    continue
            except Exception:
                continue
            try:
                if window.isMiniaturized():
                    continue
            except Exception:
                pass
            try:
                title = str(window.title() or "")
            except Exception:
                title = ""
            if title == "Spacing Preview":
                continue
            # A normal visible Glyphs document/edit window is enough.
            return True
        return False

    def _sync_visibility(self, make_key=False):
        if self.window is None:
            return
        try:
            app_is_active = bool(NSApplication.sharedApplication().isActive())
        except Exception:
            app_is_active = True
        should_show = app_is_active and _has_active_edit_view() and self._has_visible_host_window()

        if should_show:
            if not self.window.isVisible():
                if make_key:
                    self.window.makeKeyAndOrderFront_(None)
                else:
                    self.window.orderFront_(None)
        elif self.window.isVisible():
            self.window.orderOut_(None)

    def applicationDidBecomeActive_(self, notification):
        self._sync_visibility()
        self.update_(None)

    def applicationDidResignActive_(self, notification):
        if self.window is not None and self.window.isVisible():
            self.window.orderOut_(None)

    def applicationWillTerminate_(self, notification):
        self.close()

    def windowWillCloseNotification_(self, notification):
        try:
            closing_window = notification.object()
        except Exception:
            closing_window = None
        if self.window is not None and closing_window != self.window:
            # If the font/document window is closing, hide immediately.
            try:
                if self.window.isVisible():
                    self.window.orderOut_(None)
            except Exception:
                pass

    def windowDidBecomeKeyNotification_(self, notification):
        self._sync_visibility()
        self.update_(None)

    def _remove_callback(self):
        if self._callback is not None:
            try:
                Glyphs.removeCallback(self._callback)
            except Exception:
                pass
            self._callback = None
        for callback in list(self._glyphsCallbacks):
            try:
                Glyphs.removeCallback(callback)
            except Exception:
                pass
        self._glyphsCallbacks = []

    def update_(self, sender):
        self._sync_visibility()
        if self.drawView is not None and self.window is not None and self.window.isVisible():
            self.drawView.setNeedsDisplay_(True)

    def toggleTheme_(self, sender):
        if self.drawView is None:
            return
        self.drawView.darkMode = not self.drawView.darkMode
        Glyphs.defaults[KEY_DARK_MODE] = bool(self.drawView.darkMode)
        if self.themeButton is not None:
            self.themeButton.setTitle_("Theme: Black" if self.drawView.darkMode else "Theme: White")
        self.drawView.setNeedsDisplay_(True)

    def toggleKerning_(self, sender):
        if self.drawView is None:
            return
        self.drawView.kerningOn = not self.drawView.kerningOn
        Glyphs.defaults[KEY_KERNING_ON] = bool(self.drawView.kerningOn)
        if self.kerningButton is not None:
            self.kerningButton.setTitle_("Kerning: On" if self.drawView.kerningOn else "Kerning: Off")
        self.drawView.setNeedsDisplay_(True)

    def cycleString_(self, sender):
        if self.drawView is None:
            return
        self.drawView.stringIndex = (int(self.drawView.stringIndex) + 1) % len(TEMPLATE_LINES)
        Glyphs.defaults[KEY_STRING_INDEX] = int(self.drawView.stringIndex)
        if self.stringButton is not None:
            self.stringButton.setTitle_(self._string_button_title())
        self.drawView.setNeedsDisplay_(True)

    def windowWillClose_(self, notification):
        self._remove_callback()
        self._remove_notifications()
        self.window = None
        self.drawView = None
        self.themeButton = None
        self.kerningButton = None
        self.stringButton = None


class PilsSpacingFloatingPanel(GeneralPlugin):

    @objc.python_method
    def settings(self):
        self.name = Glyphs.localize({
            "en": "Spacing Preview",
            "cs": "Spacing Preview",
        })
        _ensure_default_bool(KEY_DARK_MODE, True)
        _ensure_default_bool(KEY_KERNING_ON, True)
        _ensure_default_int(KEY_STRING_INDEX, 0)
        self.controller = None

        self.menuItem = NSMenuItem.alloc().init()
        self.menuItem.setTitle_(self.name)
        self.menuItem.setTarget_(self)
        self.menuItem.setAction_(self.togglePanel_)

    @objc.python_method
    def start(self):
        Glyphs.menu[EDIT_MENU].append(self.menuItem)

    def togglePanel_(self, sender):
        if self.controller is not None and self.controller.isOpen():
            self.controller.close()
        else:
            self.controller = PilsSpacingPanelController.alloc().init()
            self.controller.open()
        self.updateMenuState()

    @objc.python_method
    def updateMenuState(self):
        if hasattr(self, "menuItem"):
            is_open = self.controller is not None and self.controller.isOpen()
            self.menuItem.setState_(ONSTATE if is_open else OFFSTATE)

    def validateMenuItem_(self, menuItem):
        self.updateMenuState()
        return Glyphs.font is not None

    @objc.python_method
    def __file__(self):
        return __file__
