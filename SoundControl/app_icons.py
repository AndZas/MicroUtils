"""One antialiased microphone drawing for the sidebar, taskbar, tray and EXE.

Pillow keeps this renderer safe on the audio worker as well as the Qt thread.
Coordinates use a 32-unit canvas; supersampling also keeps small tray icons crisp.
"""
from functools import lru_cache
from PIL import Image, ImageDraw

GREEN, RED, AMBER, MUTED = '#57ce91', '#ff929d', '#f5cc88', '#a2afc4'


def connection_color(state, present=None, pending=False):
    if state is None or present is False:
        return RED
    if present is None or pending:
        return AMBER
    return GREEN


@lru_cache(maxsize=32)
def _render(state, size):
    # Reconstruct the supplied pre-polish-5 icon, enlarged around its ink centre.
    # Drawing from geometry avoids magnifying the original 64px staircase.
    scale = size * 4 / 64
    image = Image.new('RGBA', (size * 4, size * 4))
    draw = ImageDraw.Draw(image)
    color = RED if state is True else GREEN if state is False else AMBER
    def point(x, y):
        return (round((32+(x-32)*1.1)*scale), round((32+(y-34)*1.1)*scale))

    def box(coords):
        return (*point(*coords[:2]), *point(*coords[2:]))

    def line(coords, stroke=4, ink=None):
        points = [point(*p) for p in coords]
        width = max(1, round(stroke*1.1*scale))
        ink = color if ink is None else ink
        draw.line(points, fill=ink, width=width, joint='curve')
        radius = width / 2
        for x, y in (points[0], points[-1]):
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=ink)

    draw.rounded_rectangle(box((23, 7, 41, 37)), radius=round(9*1.1*scale), fill=color)
    draw.arc(box((15, 19, 49, 47)), 0, 180, fill=color, width=round(5*1.1*scale))
    line(((32, 45), (32, 56)))
    line(((22, 57), (42, 57)))
    if state is True:
        # Opposite diagonal avoids the sidebar's lower-right connection dot.
        # A transparent gap separates a colour-matched slash from the capsule.
        slash = ((12, 54), (52, 12))
        line(slash, 8, (0,0,0,0))
        line(slash, 3.6)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def microphone_image(state=False, dot_color=None, size=64):
    # Connection status belongs to the sidebar, never the tray/taskbar glyph.
    return _render(state, size).copy()


if __name__ == '__main__':
    from pathlib import Path
    microphone_image(size=256).save(Path(__file__).with_name('app.ico'),
        sizes=[(16,16), (20,20), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)])
