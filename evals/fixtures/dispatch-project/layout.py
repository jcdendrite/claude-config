from components import WidgetRenderer

_renderer = WidgetRenderer()


def build_layout(layout_id):
    return _renderer.render_layout(layout_id)
