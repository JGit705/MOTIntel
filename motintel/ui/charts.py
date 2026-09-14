"""Plotly styling shared by every chart on the page.

Kept apart from the app so a chart reads the same wherever it is drawn, and so
the page script holds the charts' content rather than their chrome.
"""
from __future__ import annotations

import plotly.graph_objects as go


def style_fig(fig: go.Figure, p: dict, height: int) -> go.Figure:
    fig.update_layout(
        template=p["plotly"], height=height,
        margin=dict(l=6, r=6, t=6, b=6),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        # The page's own face. Plotly otherwise sets its axes in Open Sans, a
        # second sans a few pixels different from every label around it.
        font=dict(color=p["text"], size=12,
                  family='"Source Sans", "Source Sans Pro", sans-serif'),
        showlegend=False,
        hoverlabel=dict(font_size=12))
    fig.update_xaxes(gridcolor=p["grid"], zeroline=False)
    fig.update_yaxes(gridcolor=p["grid"], zeroline=False)
    return fig
