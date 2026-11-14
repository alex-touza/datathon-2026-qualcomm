"""Original visual math cases with deterministic, independently scored answers."""

from itertools import combinations
from math import comb
from random import Random
from PIL import Image, ImageDraw, ImageFont

INK = "#14213d"
BLUE = "#175cd3"
ORANGE = "#c0550c"


def label(draw, x, y, value, size=30, color=INK):
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.truetype("DejaVuSans.ttf", size)
    draw.text((x, y), str(value), font=font, fill=color, anchor="mm")


def grid(draw, columns, rows, cell, x, y, shaded=()):
    for col in range(columns):
        for row in range(rows):
            left, top = x + col * cell, y + row * cell
            draw.rectangle((left, top, left + cell, top + cell),
                           fill=BLUE if (col, row) in shaded else "#f8fafc",
                           outline=INK, width=2)


def bars(draw, values):
    draw.line((90, 390, 650, 390), fill=INK, width=3)
    for i, value in enumerate(values):
        x = 130 + i * 145
        y = 388 - round(270 * value / max(values))
        draw.rectangle((x - 35, y, x + 35, 388), fill=BLUE)
        label(draw, x, y - 20, value)
        label(draw, x, 426, "ABCD"[i])


def table(draw, rows):
    width, height = 572 // len(rows[0]), 76
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            x, y = 64 + width * j, 64 + height * i
            draw.rectangle((x, y, x + width, y + height),
                           fill="#e4efff" if i == 0 else "#ffffff", outline=INK, width=2)
            label(draw, x + width / 2, y + height / 2, value, 26)


def graph(draw, points, edges):
    for a, b, weight in edges:
        x, y = points[a]
        u, v = points[b]
        draw.line((x, y, u, v), fill="#62748a", width=4)
        if weight is not None:
            cx, cy = (x + u) / 2, (y + v) / 2
            draw.rounded_rectangle((cx - 18, cy - 16, cx + 18, cy + 16), radius=8, fill="white")
            label(draw, cx, cy, weight, 24)
    for name, (x, y) in points.items():
        draw.ellipse((x - 25, y - 25, x + 25, y + 25), fill=BLUE)
        label(draw, x, y, name, 27, "white")


def count_shapes(draw, rng, second):
    n = rng.randint(11, 15)
    blue = rng.randint(4, n - 4)
    for i, is_blue in enumerate(rng.sample([True] * blue + [False] * (n - blue), n)):
        x, y = 110 + (i % 5) * 121, 102 + (i // 5) * 116
        if is_blue:
            draw.ellipse((x - 28, y - 28, x + 28, y + 28), fill=BLUE)
        else:
            draw.rectangle((x - 25, y - 25, x + 25, y + 25), fill=ORANGE)
    if second:
        return "How many orange squares are shown?", n - blue
    return "How many blue circles are shown?", blue


def simple_chart(draw, rng, second):
    values = rng.sample(range(3, 19), 4)
    bars(draw, values)
    if second:
        return "How many more items does the tallest bar represent than the shortest bar?", max(values) - min(values)
    return "How many items do groups A and C have altogether?", values[0] + values[2]


def tiles(draw, rng, second):
    columns, rows = rng.randint(4, 7), rng.randint(3, 5)
    cut_x, cut_y = rng.randint(1, columns - 1), rng.randint(1, rows - 1)
    shaded = {(x, y) for x in range(columns) for y in range(rows)
              if x < cut_x or y >= cut_y}
    grid(draw, columns, rows, 61, 125, 75, shaded)
    if second:
        edges = sum((x + dx, y + dy) not in shaded for x, y in shaded
                    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))
        return "Each grid square has side length 1. What is the perimeter of the blue shape?", edges
    return "Each grid square has area 1. What is the area of the blue shape?", len(shaded)


def scaled_table(draw, rng, second):
    if second:
        prices = rng.sample(range(3, 15), 3)
        quantities = rng.sample(range(2, 9), 3)
        table(draw, [("Item", "Price each", "Quantity"),
                     *((name, price, qty) for name, price, qty in zip("ABC", prices, quantities))])
        return "The price is in coins. How many coins does it cost to buy the quantities shown for A and C only?", (
            prices[0] * quantities[0] + prices[2] * quantities[2])
    totals = [rng.randint(6, 15) for _ in range(3)]
    groups = [rng.randint(3, 6) for _ in range(3)]
    table(draw, [("Team", "Bags", "Per bag"),
                 *((name, n, size) for name, n, size in zip("ABC", groups, totals))])
    return "The table shows bags of marbles and the number of marbles per bag. How many marbles do the three teams have altogether?", sum(a * b for a, b in zip(totals, groups))


def data_changes(draw, rng, second):
    initial = rng.randint(34, 59)
    rates = rng.sample(range(3, 17), 3)
    lengths = rng.sample(range(2, 9), 3)
    if second:
        table(draw, [("Segment", "Speed (km/h)", "Hours"),
                     *((tag, v, t) for tag, v, t in zip("ABC", rates, lengths))])
        return "A cyclist travels through the three segments shown. What is the total distance in kilometres?", sum(
            v * t for v, t in zip(rates, lengths))
    table(draw, [("Start", "Change 1", "Change 2", "Change 3"),
                 (initial, f"+{rates[0] * lengths[0]}", f"-{rates[1]}", f"+{rates[2] * lengths[2]}")])
    return "An account starts with the balance shown and undergoes all three changes. What is its final balance?", (
        initial + rates[0] * lengths[0] - rates[1] + rates[2] * lengths[2])


def lattice(draw, rng, second):
    width, height = rng.randint(3, 6), rng.randint(3, 5)
    blocked = set(rng.sample([(x, y) for x in range(1, width) for y in range(1, height)], 2))
    cell, x0, y0 = 59, 153, 75
    grid(draw, width, height, cell, x0, y0)
    for x, y in blocked:
        cx, cy = x0 + x * cell, y0 + y * cell
        draw.line((cx - 9, cy - 9, cx + 9, cy + 9), fill="#a92021", width=5)
        draw.line((cx - 9, cy + 9, cx + 9, cy - 9), fill="#a92021", width=5)
    label(draw, x0 - 30, y0, "S", 25)
    label(draw, x0 + width * cell + 32, y0 + height * cell, "T", 25)
    ways = {(0, 0): 1}
    for x in range(width + 1):
        for y in range(height + 1):
            if (x, y) != (0, 0):
                ways[x, y] = (0 if (x, y) in blocked else
                              ways.get((x - 1, y), 0) + ways.get((x, y - 1), 0))
    prompt = "How many routes from S to T follow grid lines, move only right or down, and avoid both intersections marked with a red cross?"
    if second:
        prompt = "How many otherwise valid right-or-down routes from S to T are ruled out because they pass through at least one intersection marked with a red cross?"
        return prompt, comb(width + height, width) - ways[width, height]
    return prompt, ways[width, height]


def colored_draws(draw, rng, second):
    sizes = rng.sample(range(3, 10), 3)
    table(draw, [("Colour", "Blue", "Orange", "Green"), ("Balls", *sizes)])
    total = sum(sizes)
    if second:
        answer = sum(a * b for a, b in combinations(sizes, 2))
        return "Treat the balls as distinct objects. How many unordered pairs of balls have different colours?", answer
    answer = total * (total - 1) // 2 - (total - sizes[0]) * (total - sizes[0] - 1) // 2
    return "Treat the balls as distinct objects. How many unordered pairs of balls contain at least one blue ball?", answer


def routes(draw, rng, second):
    points = {"S": (80, 240), "A": (249, 108), "B": (249, 370),
              "C": (443, 108), "D": (443, 370), "T": (620, 240)}
    connections = ["SA", "SB", "AC", "BD", "AD", "CT", "DT", "CD"]
    weights = [rng.randint(2, 15) for _ in connections]
    edges = [(s[0], s[1], w) for s, w in zip(connections, weights)]
    graph(draw, points, edges)
    neighbors = {p: [] for p in points}
    for a, b, weight in edges:
        neighbors[a].append((b, weight))
        neighbors[b].append((a, weight))

    def times(node, seen, elapsed):
        if node == "T":
            return [elapsed]
        return [result for nxt, weight in neighbors[node] if nxt not in seen
                for result in times(nxt, seen | {nxt}, elapsed + weight)]

    lengths = sorted(set(times("S", {"S"}, 0)))
    if second:
        return "Road weights are travel times. A route may not revisit any town. What is the second-smallest distinct travel time from S to T?", lengths[1]
    return "Road weights are travel times. What is the smallest possible total travel time from S to T?", lengths[0]


FAMILIES = (count_shapes, simple_chart, tiles, scaled_table, data_changes, lattice, colored_draws, routes)


def visual_case(level, index, rng, image_path):
    """Write a 720 × 480 PNG and return its prompt, solution and four choices."""
    image = Image.new("RGB", (720, 480), "white")
    draw = ImageDraw.Draw(image)
    prompt, answer = FAMILIES[level](draw, rng, bool(index % 2))
    choice_rng = Random(39827 + 311 * level + 113 * index)
    scratch = ImageDraw.Draw(Image.new("RGB", image.size))
    distractors = []
    for _ in range(128):
        _, alternative = FAMILIES[level](scratch, choice_rng, bool(index % 2))
        if alternative != answer and alternative not in distractors:
            distractors.append(alternative)
            if len(distractors) == 4:
                break
    else:
        raise ValueError(f"Too few alternatives for visual case {level}/{index}")
    image.save(image_path)
    return prompt, answer, distractors
