from components import WidgetRenderer

_renderer = WidgetRenderer()


def render(widget_id):
    return _renderer.render_widget(widget_id)
