"""Shared widget components."""


class WidgetRenderer:
    def render_widget(self, widget_id, context=None):
        return f"<widget id={widget_id}>"

    def render_layout(self, layout_id):
        return f"<layout id={layout_id}>"
