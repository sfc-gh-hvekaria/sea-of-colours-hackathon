"""Web layer for Sea of Colours.

A thin FastAPI app that serves the human-playable map view and exposes a
JSON endpoint backed by the existing :mod:`sea_of_colours` generator. The
server is the only thing that knows about colours and Unicode block
characters; the frontend just paints what it is told.
"""
