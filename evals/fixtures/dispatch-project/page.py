from components import WidgetRenderer

_renderer = WidgetRenderer()


def render_page(page_id):
    return _renderer.render_widget(f"page-{page_id}")
