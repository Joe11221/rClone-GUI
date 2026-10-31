"""Regenerate desktop/assets/icon.ico. Run from the repo root."""

from pathlib import Path

from PIL import Image, ImageDraw

SCALE = 4  # Draw large and downsample for smooth edges.
SIZE = 256 * SCALE
BACKGROUND = (13, 110, 253, 255)
FOREGROUND = (255, 255, 255, 255)


def scaled(*values):
    return [value * SCALE for value in values]


image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle(scaled(0, 0, 256, 256), radius=48 * SCALE, fill=BACKGROUND)

# Cloud
draw.ellipse(scaled(40, 96, 124, 180), fill=FOREGROUND)
draw.ellipse(scaled(84, 56, 176, 148), fill=FOREGROUND)
draw.ellipse(scaled(136, 92, 216, 172), fill=FOREGROUND)
draw.rounded_rectangle(scaled(64, 124, 192, 180), radius=20 * SCALE, fill=FOREGROUND)

# Upload arrow
draw.polygon(scaled(128, 84, 164, 128, 92, 128), fill=BACKGROUND)
draw.rectangle(scaled(114, 124, 142, 164), fill=BACKGROUND)

output = Path("desktop/assets/icon.ico")
output.parent.mkdir(exist_ok=True)
image.resize((256, 256), Image.LANCZOS).save(
    output, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
)
