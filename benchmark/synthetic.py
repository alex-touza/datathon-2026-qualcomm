#!/usr/bin/env python3
"""Build original text and image problems for the hosted decision comparison."""
import argparse
import json
import random
from pathlib import Path
import hashlib

from PIL import Image, ImageDraw, ImageFont

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

EXPANDED_TEXT = {
    "eng": [
        "A library has {a} books on one shelf and {b} on another. Then {c} books are borrowed. How many remain?",
        "A club buys {a} packs with {b} cards in each pack, then gets {c} more cards. How many cards does it have?",
        "A cyclist rides {a} kilometres each hour for {b} hours, then rides {c} more kilometres. How far does the cyclist travel?",
        "A ribbon is {a} centimetres long. Maya uses {b} centimetres and cuts off {c} more. How many centimetres remain?",
        "A baker has {a} trays with {b} muffins on each. The muffins are shared equally among {c} tables. How many muffins go to each table?",
        "A map uses {a} centimetres for {b} kilometres. Two towns are {c} centimetres apart on the map. How many kilometres apart are they?",
        "A tank is {a} litres full. A pump adds {b} litres per minute for {c} minutes. How many litres are in the tank now?",
        "A team scores {a} points in each of {b} rounds and then loses {c} points for a penalty. What is its final score?",
        "A rectangle is {a} centimetres long and {b} centimetres wide. What is its perimeter?",
        "A floor is {a} metres long and {b} metres wide. How many one-square-metre tiles cover it?",
        "A shop mixes {a} litres of juice with {b} litres of water. It pours the mixture equally into {c} bottles. How many litres go in each bottle?",
        "A number is multiplied by {a}, then {b} is added. The result is {c}. What was the number?",
        "A class has {a} students. {b} percent are in the art club. How many students are in the art club?",
        "A printer makes {a} pages every {b} minutes. At the same rate, how many pages does it make in {c} minutes?",
        "A rectangular garden is {a} metres by {b} metres. A path of width {c} metre runs all the way around the outside. What is the outer perimeter?",
        "A number is divided by {a}, leaving remainder {b}. What remainder is left when twice that number is divided by {a}?",
    ],
    "cat": [
        "Una biblioteca té {a} llibres en un prestatge i {b} en un altre. Se n’enduen {c}. Quants en queden?",
        "Un club compra {a} paquets amb {b} cartes cadascun i després n’obté {c} més. Quantes cartes té?",
        "Un ciclista recorre {a} quilòmetres cada hora durant {b} hores i després en fa {c} més. Quina distància recorre?",
        "Una cinta fa {a} centímetres. La Maya n’utilitza {b} i en talla {c} més. Quants centímetres queden?",
        "Un forner té {a} safates amb {b} magdalenes cadascuna. Les reparteix igualment entre {c} taules. Quantes en rep cada taula?",
        "En un mapa, {a} centímetres representen {b} quilòmetres. Dos pobles són a {c} centímetres al mapa. Quants quilòmetres els separen?",
        "Un dipòsit conté {a} litres. Una bomba hi afegeix {b} litres per minut durant {c} minuts. Quants litres hi ha ara?",
        "Un equip fa {a} punts en cadascuna de {b} rondes i en perd {c} per una penalització. Quants punts té al final?",
        "Un rectangle fa {a} centímetres de llarg i {b} d’ample. Quin és el seu perímetre?",
        "Un terra fa {a} metres de llarg i {b} d’ample. Quantes rajoles d’un metre quadrat calen per cobrir-lo?",
        "Una botiga barreja {a} litres de suc amb {b} litres d’aigua. Reparteix la barreja igualment en {c} ampolles. Quants litres van a cada ampolla?",
        "Multipliquem un nombre per {a} i després hi sumem {b}. El resultat és {c}. Quin era el nombre?",
        "Una classe té {a} alumnes. El {b} per cent són al club d’art. Quants alumnes hi són?",
        "Una impressora fa {a} pàgines cada {b} minuts. Al mateix ritme, quantes pàgines fa en {c} minuts?",
        "Un jardí rectangular fa {a} metres per {b}. Un camí d’un metre d’amplada l’envolta. Quin és el perímetre exterior?",
        "En dividir un nombre per {a}, el residu és {b}. Quin residu queda en dividir el doble del nombre per {a}?",
    ],
    "esp": [
        "Una biblioteca tiene {a} libros en una estantería y {b} en otra. Se prestan {c}. ¿Cuántos quedan?",
        "Un club compra {a} paquetes con {b} cartas cada uno y luego recibe {c} más. ¿Cuántas cartas tiene?",
        "Una ciclista recorre {a} kilómetros por hora durante {b} horas y después recorre {c} más. ¿Qué distancia recorre?",
        "Una cinta mide {a} centímetros. Maya usa {b} y corta {c} más. ¿Cuántos centímetros quedan?",
        "Un panadero tiene {a} bandejas con {b} magdalenas cada una. Las reparte por igual entre {c} mesas. ¿Cuántas recibe cada mesa?",
        "En un mapa, {a} centímetros representan {b} kilómetros. Dos pueblos están a {c} centímetros en el mapa. ¿A cuántos kilómetros están?",
        "Un depósito contiene {a} litros. Una bomba añade {b} litros por minuto durante {c} minutos. ¿Cuántos litros hay ahora?",
        "Un equipo consigue {a} puntos en cada una de {b} rondas y pierde {c} por una penalización. ¿Cuál es su puntuación final?",
        "Un rectángulo mide {a} centímetros de largo y {b} de ancho. ¿Cuál es su perímetro?",
        "Un suelo mide {a} metros de largo y {b} de ancho. ¿Cuántas baldosas de un metro cuadrado lo cubren?",
        "Una tienda mezcla {a} litros de zumo con {b} litros de agua. Reparte la mezcla por igual en {c} botellas. ¿Cuántos litros van en cada botella?",
        "Multiplicamos un número por {a} y luego sumamos {b}. El resultado es {c}. ¿Cuál era el número?",
        "Una clase tiene {a} estudiantes. El {b} por ciento participa en el club de arte. ¿Cuántos estudiantes participan?",
        "Una impresora produce {a} páginas cada {b} minutos. Al mismo ritmo, ¿cuántas páginas produce en {c} minutos?",
        "Un jardín rectangular mide {a} metros por {b}. Un camino de un metro de ancho lo rodea. ¿Cuál es el perímetro exterior?",
        "Al dividir un número entre {a}, queda resto {b}. ¿Qué resto queda al dividir el doble del número entre {a}?",
    ],
}

CHART_PROMPTS = {
    "eng": [
        "The bar chart shows the number of books read by five groups. How many did group {x} and group {y} read altogether?",
        "The chart shows points earned by five teams. How many more points did teams {x} and {y} earn together than teams {z} and {w}?",
    ],
    "cat": [
        "El gràfic de barres mostra quants llibres han llegit cinc grups. Quants n’han llegit en total els grups {x} i {y}?",
        "El gràfic mostra els punts de cinc equips. Quants punts més han fet junts els equips {x} i {y} que els equips {z} i {w}?",
    ],
    "esp": [
        "El gráfico de barras muestra cuántos libros han leído cinco grupos. ¿Cuántos han leído en total los grupos {x} e {y}?",
        "El gráfico muestra los puntos de cinco equipos. ¿Cuántos puntos más han conseguido juntos los equipos {x} e {y} que los equipos {z} y {w}?",
    ],
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


def render_chart(path, values, title):
    """Draw a readable labeled bar chart; canvas exceeds hosted vision minimums."""
    canvas = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=28)
        small_font = ImageFont.load_default(size=21)
    except TypeError:  # Pillow before scalable default fonts
        font = ImageFont.truetype("DejaVuSans.ttf", 28)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 21)
    draw.text((30, 18), title, fill="#172554", font=small_font)
    draw.line((70, 430, 750, 430), fill="#334155", width=3)
    max_value = max(values)
    for i, value in enumerate(values):
        x = 105 + i * 130
        height = int(300 * value / max_value)
        draw.rectangle((x, 430 - height, x + 72, 430), fill=("#2563eb", "#0f766e", "#d97706", "#7c3aed", "#be123c")[i])
        draw.text((x + 24, 440), "ABCDE"[i], fill="#111827", font=font)
        draw.text((x + 11, 430 - height - 34), str(value), fill="#111827", font=font)
    canvas.save(path)


def expanded_problem(level, variant, rng):
    """Return a distinct arithmetic problem and its exact integer solution."""
    t = level * 2 + variant
    if t == 0:  # addition/subtraction
        a, b, c = rng.randint(35, 70), rng.randint(15, 45), rng.randint(5, 20)
        return (a, b, c), a + b - c
    if t == 1:  # multiplication and addition
        a, b, c = rng.randint(4, 9), rng.randint(6, 12), rng.randint(8, 25)
        return (a, b, c), a * b + c
    if t == 2:  # rate over time
        a, b, c = rng.randint(12, 28), rng.randint(3, 7), rng.randint(5, 15)
        return (a, b, c), a * b + c
    if t == 3:  # subtraction in a context
        a, b, c = rng.randint(75, 150), rng.randint(20, 45), rng.randint(5, 18)
        return (a, b, c), a - b - c
    if t == 4:  # equal sharing
        c, per_table, b = rng.randint(3, 8), rng.randint(4, 12), rng.randint(4, 10)
        a = c * per_table
        return (a, b, c), per_table * b
    if t == 5:  # scale conversion
        a, b = rng.randint(2, 8), rng.randint(3, 9)
        c = a * rng.randint(2, 8)
        return (a, b, c), b * c // a
    if t == 6:  # repeated addition
        a, b, c = rng.randint(15, 40), rng.randint(3, 8), rng.randint(10, 35)
        return (a, b, c), a + b * c
    if t == 7:  # penalty
        a, b, c = rng.randint(12, 30), rng.randint(4, 9), rng.randint(5, 25)
        return (a, b, c), a * b - c
    if t == 8:  # perimeter
        a, b = rng.randint(12, 35), rng.randint(6, 20)
        return (a, b, 0), 2 * (a + b)
    if t == 9:  # area
        a, b = rng.randint(8, 24), rng.randint(7, 18)
        return (a, b, 0), a * b
    if t == 10:  # ratio / equal volume
        c, a, b = rng.randint(3, 8), rng.randint(8, 24), rng.randint(5, 20)
        total = a + b
        if total % c:
            a += c - total % c
        return (a, b, c), (a + b) // c
    if t == 11:  # inverse operation
        a, b, x = rng.randint(3, 9), rng.randint(10, 35), rng.randint(8, 25)
        return (a, b, a * x + b), x
    if t == 12:  # exact percent
        a = rng.choice([80, 100, 120, 160, 200, 240])
        b = rng.choice([15, 20, 25, 30, 40])
        return (a, b, 0), a * b // 100
    if t == 13:  # proportional rate
        a, b, k = rng.randint(30, 90), rng.randint(3, 9), rng.randint(2, 6)
        return (a, b, b * k), a * k
    if t == 14:  # outer perimeter with one-metre path
        a, b = rng.randint(10, 24), rng.randint(8, 18)
        return (a, b, 1), 2 * ((a + 2) + (b + 2))
    # modular arithmetic, with a deliberately known remainder
    a, q, r = rng.randint(7, 13), rng.randint(8, 20), rng.randint(1, 5)
    return (a, r, a * q + r), (2 * r) % a


def make_options(answer, correct_index, rng):
    # Draw wrong choices uniformly from a fixed-width magnitude band containing
    # the answer. Their distances from the answer are not a reusable signature.
    low = (answer // 50) * 50
    high = low + 49
    pool = [value for value in range(low, high + 1) if value != answer]
    distractors = rng.sample(pool, 4)
    choices = list(distractors)
    choices.insert(correct_index, answer)
    return dict(zip("ABCDE", map(str, choices)))


def write_expanded(output, images, rng):
    positions = ([0, 1, 2, 3, 4] * 6 + [0, 1])
    rng.shuffle(positions)
    position_index = 0
    rows = []
    for level in range(8):
        for variant in range(2):
            params, answer = expanded_problem(level, variant, rng)
            correct_index = positions[position_index]
            problem_id = f"expanded-L{level}-T{variant + 1}"
            choice_rng = random.Random(int.from_bytes(hashlib.sha256(problem_id.encode()).digest()[:8], "big"))
            options = make_options(answer, correct_index, choice_rng)
            for language in ("eng", "cat", "esp"):
                row = {"id": problem_id, "language": language, "level": level,
                       "kind": "text", "text_only": True,
                       "text": EXPANDED_TEXT[language][level * 2 + variant].format(a=params[0], b=params[1], c=params[2]),
                       "options": options, "reviewed_options": options,
                       "label": "ABCDE"[correct_index],
                       "image": None, "dataset_origin": "synthetic"}
                rows.append(row)
            position_index += 1
        for variant in range(2):
            # Visual datasets become denser at higher levels while labels remain explicit.
            values = [rng.randint(2 + level, 8 + level * 3) for _ in range(5)]
            if variant == 0:
                answer = values[0] + values[1]
                prompt_index = 0
            else:
                answer = values[0] + values[1] - values[2] - values[3]
                if answer <= 0:
                    values[0] += abs(answer) + 2
                    answer = values[0] + values[1] - values[2] - values[3]
                prompt_index = 1
            correct_index = positions[position_index]
            problem_id = f"expanded-L{level}-I{variant + 1}"
            choice_rng = random.Random(int.from_bytes(hashlib.sha256(problem_id.encode()).digest()[:8], "big"))
            options = make_options(answer, correct_index, choice_rng)
            image = f"images/{problem_id}.png"
            render_chart(images.parent / image, values, f"Level {level} - {('Books read', 'Team points')[variant]}")
            x, y, z, w = ("A", "B", "C", "D")
            prompt_values = {"x": x, "y": y, "z": z, "w": w}
            position_index += 1
            for language in ("eng", "cat", "esp"):
                row = {"id": problem_id, "language": language, "level": level,
                       "kind": "figure", "text_only": False,
                       "text": CHART_PROMPTS[language][prompt_index].format(**prompt_values),
                       "options": options, "reviewed_options": options,
                       "label": "ABCDE"[correct_index], "image": image,
                       "dataset_origin": "synthetic"}
                rows.append(row)
    for row in rows:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expanded", action="store_true", help="append 32 new problems (4 per level band) to the original 12")
    args = parser.parse_args()
    images = args.out / "images"
    images.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)
    correct_positions = [0, 1, 2, 3, 4] * 2 + [0, 1]
    rng.shuffle(correct_positions)
    with (args.out / "pilot.jsonl").open("w", encoding="utf-8") as output:
        for index in range(12):
            case = index % 4
            a, b, c = rng.randint(3, 5), rng.randint(4, 8), rng.randint(2, 4)
            count = (a + b, b, a * b - c, a * 4 + c % a)[case]
            choices = [value for value in range(count - 2, count + 3) if value != count]
            rng.shuffle(choices)
            choices.insert(correct_positions[index], count)
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
        if args.expanded:
            write_expanded(output, images, random.Random(4202))


if __name__ == "__main__":
    main()
