import gtk
import gobject
import cairo
import socket
import os
import os.path

class ClientWindow(gtk.Window):
    def __init__(self, protocol, id, x, y, w, h, metadata):
        gtk.Window.__init__(self)
        self._protocol = protocol
        self._id = id
        self._pos = (-1, -1)
        self._size = (1, 1)
        self._backing = None
        self._metadata = {}
        self._new_backing(1, 1)
        self.update_metadata(metadata)
        
        self.set_app_paintable(True)

        # FIXME: It's possible in X to request a starting position for a
        # window, but I don't know how to do it from GTK.
        self.set_default_size(w, h)

    def update_metadata(self, metadata):
        self._metadata.update(metadata)
        
        self.set_title(u"%s (via XScreen)"
                       % self._metadata.get("title",
                                            "<untitled window>"
                                            ).decode("utf-8"))
        hints = {}
        for (a, h1, h2) in [
            ("size-constraint:maximum-size", "max_width", "max_height"),
            ("size-constraint:minimum-size", "min_width", "min_height"),
            ("size-constraint:base-size", "base_width", "base_height"),
            ("size-constraint:increment", "width_inc", "height_inc"),
            ]:
            if a in self._metadata:
                hints[h1], hints[h2] = self._metadata[a]
        if hints:
            self.set_geometry_hints(None, **hints)

    def _new_backing(self, w, h):
        old_backing = self._backing
        self._backing = gtk.gdk.Pixmap(gtk.gdk.get_default_root_window(),
                                       w, h)
        if old_backing is not None:
            # Really we should respect bit-gravity here but... meh.
            cr = self._backing.cairo_create()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_pixmap(old_backing, 0, 0)
            cr.paint()
            old_w, old_h = old_backing.get_size()
            cr.move_to(old_w, 0)
            cr.line_to(w, 0)
            cr.line_to(w, h)
            cr.line_to(0, h)
            cr.line_to(0, old_h)
            cr.line_to(old_w, old_h)
            cr.close_path()
            cr.set_source_rgb(1, 1, 1)
            cr.fill()

    def draw(self, x, y, width, height, rgb_data):
        assert len(rgb_data) == width * height * 3
        (my_width, my_height) = self.window.get_size()
        gc = self._backing.new_gc()
        self._backing.draw_rgb_image(gc, x, y, width, height,
                                     gtk.gdk.RGB_DITHER_NONE, rgb_data)
        self.window.invalidate_rect(gtk.gdk.Rectangle(x, y, width, height),
                                    False)

    def do_expose_event(self, event):
        if not self.flags() & gtk.MAPPED:
            return
        cr = self.window.cairo_create()
        cr.rectangle(event.area)
        cr.clip()
        cr.set_source_pixmap(self._backing, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        return False

    def _geometry(self):
        (x, y) = self.window.get_origin()
        (_, _, w, h, _) = self.window.get_geometry()
        return (x, y, w, h)

    def do_map_event(self, event):
        print "Got map event"
        gtk.Window.do_map_event(self, event)
        x, y, w, h = self._geometry()
        self._protocol.queue_packet(["map-window", self._id, x, y, w, h])
        self._pos = (x, y)
        self._size = (w, h)

    def do_configure_event(self, event):
        print "Got configure event"
        gtk.Window.do_configure_event(self, event)
        x, y, w, h = self._geometry()
        if (x, y) != self._pos:
            self._pos = (x, y)
            self._protocol.queue_packet(["move-window", self._id, x, y])
        if (w, h) != self._size:
            self._size = (w, h)
            self._protocol.queue_packet(["resize-window", self._id, w, h])
            self._new_backing(w, h)

    def do_unmap_event(self, event):
        self._protocol.queue_packet(["unmap-window", self._id])

    def do_delete_event(self, event):
        self._protocol.queue_packet(["close-window", self._id])
        return True

gobject.type_register(ClientWindow)

class XScreenClient(object):
    def __init__(self, name):
        self._window_to_id = {}
        self._id_to_window = {}

        address = os.path.expanduser("~/.xscreen/%s" % (name,))
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(address)
        print "Connected"
        self._protocol = Protocol(sock, self.process_packet)
        self._protocol.accept_packets()
        self._protocol.queue_packet(["hello", list(CAPABILITIES)])

    def _process_hello(self, packet):
        (_, capabilities) = packet
        if "deflate" in capabilities:
            self._protocol.enable_deflate()

    def _process_new_window(self, packet):
        (_, id, x, y, w, h, metadata) = packet
        window = ClientWindow(self._protocol, id, x, y, w, h, metadata)
        self._id_to_window[id] = window
        self._window_to_id[window] = id
        window.show_all()

    def _process_draw(self, packet):
        (_, id, x, y, width, height, coding, data) = packet
        window = self._id_to_window[id]
        assert coding == "rgb24"
        window.draw(x, y, width, height, data)

    def _process_window_metadata(self, packet):
        (_, id, metadata) = packet
        window = self._id_to_window[id]
        window.update_metadata(metadata)

    def _process_lost_window(self, packet):
        (_, id) = packet
        window = self._id_to_window[id]
        del self._id_to_window[id]
        del self._window_to_id[window]
        window.destroy()

    def _process_connection_lost(self, packet):
        gtk.main_quit()

    _packet_handlers = {
        "hello": _process_hello,
        "new-window": _process_new_window,
        "draw": _process_draw,
        "window-metadata": _process_window_metadata,
        "lost-window": _process_lost_window,
        Protocol.CONNECTION_LOST: _process_connection_lost,
        }
    
    def process_packet(self, packet):
        packet_type = packet[0]
        self._packet_handlers[packet_type](self, packet)