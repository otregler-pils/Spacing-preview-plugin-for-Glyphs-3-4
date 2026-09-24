# encoding: utf-8
"""Spacing Preview 1.1.0 for Glyphs 3 and 4
https://pilstype.com - MIT License
"""

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
# 6: HHxHnxnn
TEMPLATE_LINES = [
    ["n", "n", "{g}", "o", "o", "{g}", "H", "H", "{g}", "O", "O"],
    ["n", "n", "{g}", "n", "o", "{g}", "o", "o"],
    ["H", "H", "{g}", "H", "O", "{g}", "O", "O"],
    ["n", "n", "n", "{g}", "n", "n", "n"],
    ["H", "H", "H", "{g}", "H", "H", "H"],
    ["H", "H", "{g}", "H", "n", "{g}", "n", "n"],
]
WINDOW_WIDTH = 544
WINDOW_HEIGHT = 160

# Distance of the panel from the top-left corner of the Edit View
# (left edge of the window / bottom edge of the tab bar), in points.
ANCHOR_MARGIN_X = 7
ANCHOR_MARGIN_Y = 9

# Colour themes, identical to Show OHno 1.7 (both themes are exact mirrors):
#
#            background          glyphs
#   light    96.5 % white        90 % black
#   dark     96.5 % black        90 % white
#
# Grey values are (white, alpha) in 0-1, drawn with colorWithCalibratedWhite
# exactly like the reporter. "highlight" is (r, g, b, a).
THEMES = {
    "dark": {
        "background": (0.035, 0.98),
        "glyph": (0.90, 1.0),
        "highlight": (0.95, 0.62, 0.35, 1.0),
        "text": (0.90, 0.6),
        "button": (1.0, 0.10),
        "buttonText": (0.90, 0.85),
    },
    "light": {
        "background": (0.965, 0.98),
        "glyph": (0.10, 1.0),
        "highlight": (0.80, 0.42, 0.15, 1.0),
        "text": (0.10, 0.6),
        "button": (0.0, 0.06),
        "buttonText": (0.10, 0.85),
    },
}

# Same as "Highlight Selected Glyph" in Show OHno: draws the selected glyph
# in the orange highlight colour. Off by default, like in the reporter.
HIGHLIGHT_SELECTED = False


def _color(value):
    if len(value) == 2:
        white, alpha = value
        return NSColor.colorWithCalibratedWhite_alpha_(white, alpha)
    r, g, b, a = value
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def _theme_colors(dark_mode):
    theme = THEMES["dark" if dark_mode else "light"]
    return (
        _color(theme["background"]),
        _color(theme["glyph"]),
        _color(theme["text"]),
        _color(theme["highlight"]),
    )


def _style_button(button, dark_mode, title=None):
    """Flat button in theme colours instead of the system bezel."""
    if button is None:
        return
    theme = THEMES["dark" if dark_mode else "light"]
    if title is None:
        title = str(button.title())
    try:
        from AppKit import NSAttributedString, NSParagraphStyleAttributeName, NSMutableParagraphStyle
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(1)  # NSTextAlignmentCenter
        attrs = {
            NSFontAttributeName: NSFont.systemFontOfSize_(11),
            NSForegroundColorAttributeName: _color(theme["buttonText"]),
            NSParagraphStyleAttributeName: para,
        }
        button.setTitle_(title)
        button.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(title, attrs))
    except Exception:
        button.setTitle_(title)
    try:
        button.setBordered_(False)
        button.setWantsLayer_(True)
        layer = button.layer()
        layer.setBackgroundColor_(_color(theme["button"]).CGColor())
        layer.setCornerRadius_(6.0)
    except Exception:
        pass


def _apply_window_appearance(window, dark_mode):
    """Match the panel title bar and buttons to the theme."""
    if window is None:
        return
    try:
        from AppKit import NSAppearance
        name = "NSAppearanceNameDarkAqua" if dark_mode else "NSAppearanceNameAqua"
        window.setAppearance_(NSAppearance.appearanceNamed_(name))
        window.setBackgroundColor_(_theme_colors(dark_mode)[0])
    except Exception:
        pass


def _edit_window():
    """Return the NSWindow of the current font document, or None."""
    font = Glyphs.font
    if font is None:
        return None
    try:
        return font.parent.windowController().window()
    except Exception:
        pass
    try:
        return Glyphs.currentDocument.windowController().window()
    except Exception:
        return None
BUTTONS_X = 76       # right of the close/minimise/zoom buttons
BUTTON_WIDTH = 92
CONTROL_HEIGHT = 28  # control row = title bar row (buttons sit next to the close button)
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


def _active_features(tab):
    """OpenType features switched on in the Edit View (e.g. ['ss01']).

    Same logic as the Show OHno reporter.
    """
    if tab is None:
        return []
    feats = None
    try:
        feats = tab.features
    except Exception:
        feats = None
    if not feats:
        try:
            feats = tab.graphicView().features()
        except Exception:
            feats = None
    if not feats:
        return []
    result = []
    for f in feats:
        try:
            tag = str(f.name) if hasattr(f, "name") else str(f)
        except Exception:
            continue
        if tag:
            result.append(tag)
    return result


def _substituted_name(font, name, features):
    """Apply active features by Glyphs naming convention: n -> n.ss01 -> n.ss01.ss03 ...

    Works for features whose alternates use the usual suffix naming
    (ss01-ss20, salt, cv01...). Feature code itself is not interpreted.
    """
    current = name
    for tag in features:
        candidate = current + "." + tag
        g = font.glyphs[candidate]
        if g is None and current != name:
            # also allow n.ss03 when n.ss01 is applied but n.ss01.ss03 doesn't exist
            candidate = name + "." + tag
            g = font.glyphs[candidate]
        if g is not None and g.export:
            current = candidate
    return current


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

        bg, fg, text_color, highlight = _theme_colors(self.darkMode)
        accent = highlight if HIGHLIGHT_SELECTED else fg

        bg.set()
        NSBezierPath.bezierPathWithRect_(bounds).fill()

        ctx = _selected_context()
        if ctx is None:
            self._draw_message_("Select a glyph in Edit View.", text_color)
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

        try:
            features = _active_features(font.currentTab)
        except Exception:
            features = []

        for template in selected_templates:
            items = []

            for token in template:
                is_selected = token == "{g}"
                glyph_name = selected_name if is_selected else _substituted_name(font, token, features)

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
                        "selected": is_selected,
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
            self._draw_message_("No drawable layers found.", text_color)
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
                    (accent if item["selected"] else fg).set()
                    path.fill()
                    NSGraphicsContext.restoreGraphicsState()

        if missing:
            self._draw_message_("Missing: " + ", ".join(sorted(set(missing))), text_color, small=True)

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
        self._notificationsRegistered = False
        self._active = False
        return self

    def open(self):
        if self.window is not None:
            # Reopen an existing hidden window instead of creating/destroying Cocoa windows.
            # Destroying the floating window on close can crash some Glyphs/PyObjC builds.
            self._active = True
            self.window.setDelegate_(self)
            self._register_notifications()
            if self._callback is None:
                self._callback = self.update_
                try:
                    Glyphs.addCallback(self._callback, UPDATEINTERFACE)
                except Exception:
                    self._callback = None
            self._move_to_edit_window_corner()
            self._sync_visibility(make_key=True)
            self._move_to_edit_window_corner()
            self.update_(None)
            return

        style = (
            NSWindowStyleMaskTitled |
            NSWindowStyleMaskClosable |
            NSWindowStyleMaskResizable |
            NSWindowStyleMaskUtilityWindow |
            (1 << 15)  # NSWindowStyleMaskFullSizeContentView
        )
        rect = NSMakeRect(240, 240, WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Spacing Preview")
        self.window.setLevel_(NSFloatingWindowLevel)
        # Card look like Show OHno: one colour surface, no system title bar.
        try:
            self.window.setTitlebarAppearsTransparent_(True)
            self.window.setTitleVisibility_(1)  # NSWindowTitleHidden
            self.window.setMovableByWindowBackground_(True)
        except Exception:
            pass
        _apply_window_appearance(self.window, _default_bool(KEY_DARK_MODE, True))
        # Important: keep the Cocoa window retained by Python. Some Glyphs/PyObjC
        # combinations can crash if the floating NSWindow is released on close.
        try:
            self.window.setReleasedWhenClosed_(False)
        except Exception:
            pass
        self.window.setDelegate_(self)
        self._active = True
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

        dark = self.drawView.darkMode
        button_y = content_h - CONTROL_HEIGHT + 4
        specs = [
            ("themeButton", "Theme: Dark" if dark else "Theme: Light", "toggleTheme:"),
            ("kerningButton", "Kerning: On" if self.drawView.kerningOn else "Kerning: Off", "toggleKerning:"),
            ("stringButton", self._string_button_title(), "cycleString:"),
        ]
        x = BUTTONS_X
        for attr, title, action in specs:
            button = NSButton.alloc().initWithFrame_(NSMakeRect(x, button_y, BUTTON_WIDTH, 20))
            button.setTarget_(self)
            button.setAction_(action)
            button.setAutoresizingMask_(NSViewMaxXMargin | NSViewMinYMargin)
            _style_button(button, dark, title)
            content.addSubview_(button)
            setattr(self, attr, button)
            x += BUTTON_WIDTH + 6

        self._callback = self.update_
        Glyphs.addCallback(self._callback, UPDATEINTERFACE)
        self._move_to_edit_window_corner()
        self._sync_visibility(make_key=True)
        self._move_to_edit_window_corner()
        self.update_(None)

    def close(self):
        """Disable the panel without destroying the NSWindow.

        In Glyphs 3/4, closing and releasing a floating NSWindow from a Python
        plugin can crash the host app. Treat close as a safe hide/disable action.
        The window object is kept and reused when the menu item is triggered again.
        """
        self._active = False
        self._remove_callback()
        self._remove_notifications()
        if self.window is not None:
            try:
                if self.window.isVisible():
                    self.window.orderOut_(None)
            except Exception:
                pass

    def _edit_view_top_left(self):
        """Screen coordinates of the top-left corner of the Edit View.

        Glyphs' Edit View scrolls underneath the toolbar and tab bar, so the
        view's own frame starts at the top of the window. The window's
        contentLayoutRect ends exactly at the bottom of the tab bar, so that
        is used instead.
        """
        host = _edit_window()
        if host is None:
            return None
        try:
            frame = host.frame()
            layout = host.contentLayoutRect()
            left = frame.origin.x + layout.origin.x
            top = frame.origin.y + layout.origin.y + layout.size.height
            return left, top
        except Exception:
            return None

    def _move_to_edit_window_corner(self):
        """Place the panel in the top-left corner of the Edit View,
        with the same distance from the left and from the top."""
        if self.window is None:
            return
        corner = self._edit_view_top_left()
        if corner is None:
            return
        left, top = corner
        try:
            self.window.setFrameTopLeftPoint_(
                NSMakePoint(left + ANCHOR_MARGIN_X, top - ANCHOR_MARGIN_Y)
            )
        except Exception as e:
            print("Spacing Preview: could not position panel:", e)

    def isOpen(self):
        return bool(self._active and self.window is not None)

    def _string_button_title(self):
        if self.drawView is None:
            return "String: 1"
        index = int(self.drawView.stringIndex)
        if index < 0 or index >= len(TEMPLATE_LINES):
            index = 0
            self.drawView.stringIndex = 0
        return "String: %d" % (index + 1)

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
        if self.window is None or not self._active:
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
        # Do not call NSWindow.close() during app termination. Just detach our hooks.
        self._active = False
        self._remove_callback()
        self._remove_notifications()
        try:
            if self.window is not None and self.window.isVisible():
                self.window.orderOut_(None)
        except Exception:
            pass

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

    def update_(self, sender):
        self._sync_visibility()
        if self.drawView is not None and self.window is not None and self.window.isVisible():
            self.drawView.setNeedsDisplay_(True)

    def toggleTheme_(self, sender):
        if self.drawView is None:
            return
        self.drawView.darkMode = not self.drawView.darkMode
        Glyphs.defaults[KEY_DARK_MODE] = bool(self.drawView.darkMode)
        _apply_window_appearance(self.window, self.drawView.darkMode)
        dark = self.drawView.darkMode
        _style_button(self.themeButton, dark, "Theme: Dark" if dark else "Theme: Light")
        _style_button(self.kerningButton, dark)
        _style_button(self.stringButton, dark)
        self.drawView.setNeedsDisplay_(True)

    def toggleKerning_(self, sender):
        if self.drawView is None:
            return
        self.drawView.kerningOn = not self.drawView.kerningOn
        Glyphs.defaults[KEY_KERNING_ON] = bool(self.drawView.kerningOn)
        if self.kerningButton is not None:
            _style_button(self.kerningButton, self.drawView.darkMode,
                          "Kerning: On" if self.drawView.kerningOn else "Kerning: Off")
        self.drawView.setNeedsDisplay_(True)

    def cycleString_(self, sender):
        if self.drawView is None:
            return
        self.drawView.stringIndex = (int(self.drawView.stringIndex) + 1) % len(TEMPLATE_LINES)
        Glyphs.defaults[KEY_STRING_INDEX] = int(self.drawView.stringIndex)
        if self.stringButton is not None:
            _style_button(self.stringButton, self.drawView.darkMode, self._string_button_title())
        self.drawView.setNeedsDisplay_(True)

    def windowShouldClose_(self, sender):
        # Red close button = disable/hide panel. Return False so AppKit does not
        # actually destroy the NSWindow. This avoids Glyphs crashes on close.
        self.close()
        return False

    def windowWillClose_(self, notification):
        # Only used during app shutdown or unusual OS-level close. Avoid touching
        # Cocoa objects aggressively here.
        self._active = False
        self._remove_callback()
        self._remove_notifications()


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
