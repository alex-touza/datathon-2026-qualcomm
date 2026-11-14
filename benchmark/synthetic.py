#!/usr/bin/env python3
"""Build original text and image problems for the hosted decision comparison."""
import argparse
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw

PROMPTS = {
    "eng": (
        "Nora has {a} shells and gets {b} more. How many shells does she have?",
        "How many blue circles are in the image?",
        "There are {a} boxes with {b} beads each. Then {c} beads are removed. How many remain?",
        "Each complete column in the diagram holds {a} squares. How many squares are shown in total?",
    ),
    "cat": (
        "La Nora té {a} petxines i en rep {b} més. Quantes petxines té?",
        "Quants cercles blaus hi ha a la imatge?",
        "Hi ha {a} capses amb {b} comptes cadascuna. En traiem {c}. Quants en queden?",
        "Cada columna completa del dibuix conté {a} quadrats. Quants quadrats s'hi veuen en total?",
    ),
    "esp": (
        "Nora tiene {a} conchas y recibe {b} más. ¿Cuántas conchas tiene?",
        "¿Cuántos círculos azules hay en la imagen?",
        "Hay {a} cajas con {b} cuentas cada una. Se retiran {c}. ¿Cuántas quedan?",
        "Cada columna completa del dibujo contiene {a} cuadrados. ¿Cuántos cuadrados se ven en total?",
    ),
}


def render(path, case, count, rows, rng):
    canvas = Image.new("RGB", (350, 300), "white")
    draw = ImageDraw.Draw(canvas)
    if case == 1:
        chosen = set(rng.sample(range(12), count))
        for i in range(12):
            x, y = 28 + i % 4 * 80, 26 + i // 4 * 76
            draw.ellipse((x, y, x + 42, y + 42), fill="#194fd6" if i in chosen else "#b43e08")
    else:
        for i in range(count):
            x, y = 28 + i // rows * 49, 202 - i % rows * 37
            draw.rectangle((x, y, x + 23, y + 23), fill="#126e59")
    canvas.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    images = args.out / "images"
    images.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    with (args.out / "pilot.jsonl").open("w", encoding="utf-8") as output:
        for index in range(12):
            case = index % 4
            a, b, c = rng.randint(3, 5), rng.randint(4, 8), rng.randint(2, 4)
            count = (a + b, b, a * b - c, a * 4 + c % a)[case]
            choices = list(range(count - 2, count + 3))
            rng.shuffle(choices)
            options = dict(zip("ABCDE", map(str, choices)))
            problem_id = f"original-{index + 1:02d}"
            image = None
            if case % 2:
                image = f"images/{problem_id}.png"
                render(args.out / image, case, count, a, rng)
            for language, prompts in PROMPTS.items():
                row = {
                    "id": problem_id, "language": language, "level": 1 if case < 2 else 4,
                    "kind": "figure" if image else "text", "text_only": image is None,
                    "text": prompts[case].format(a=a, b=b, c=c), "options": options,
                    "reviewed_options": options, "label": "ABCDE"[choices.index(count)],
                    "image": image, "dataset_origin": "synthetic",
                }
                output.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
