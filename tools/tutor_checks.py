#!/usr/bin/env python3
"""Fast checks for the teaching layer. No model, no network, no index.

    python3 tools/tutor_checks.py       # about a second, prints PASS or FAIL per case

These cover the promises the tutor makes in code:

* a hint that contains the correct value, the value in words, or the correct
  letter is caught; a hint that only names the method is not;
* a wrong option is diagnosed from the distractors, and the diagnosis never
  contains the correct value;
* a session refuses to check the same problem twice, refuses to jump a rung,
  and never shows the label before the student asks for the solution.

Run it after every change to copilot/tutor.py, copilot/topics.py or the servers.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from copilot import topics  # noqa: E402
from copilot.tutor import (Session, diagnose, leak_check, looks_english, numbers,  # noqa: E402
                           option_values, word_number_values)

PROBLEM = {
    "id": "area", "language": "eng", "level": 2, "text_only": False,
    "text": "A square has a perimeter of 36 cm. What is its area?",
    "options": {"A": "36 cm2", "B": "72 cm2", "C": "81 cm2", "D": "144 cm2", "E": "324 cm2"},
    "label": "C",
}
CANDY = {
    "id": "candy", "language": "eng", "level": 0, "text_only": True,
    "text": "Ann has 3 bags with 4 apples in each bag. She eats 2 apples. How many are left?",
    "options": {"A": "8", "B": "9", "C": "10", "D": "12", "E": "14"},
    "label": "C",
}
WORDY = {
    "id": "wordy", "language": "eng", "level": 1, "text_only": True,
    "text": "Which shape has four equal sides?",
    "options": {"A": "triangle", "B": "square", "C": "circle", "D": "pentagon", "E": "rhombus"},
    "label": "E",
}

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def main():
    print("Numbers and option values")
    check("decimal comma", numbers("3,5") == [3.5], numbers("3,5"))
    check("fraction", numbers("1/2") == [0.5], numbers("1/2"))
    check("cm2 is not a second number", numbers("81 cm2") == [81.0], numbers("81 cm2"))
    check("worded number", word_number_values("eighty-one") == [81], word_number_values("eighty-one"))
    check("two worded numbers stay apart",
          word_number_values("eighty-one, vuitanta-un") == [81, 81],
          word_number_values("eighty-one, vuitanta-un"))
    check("worded option has no numbers", option_values("square") == ([], ["square"]))

    print("\nThe leak check (the correct value here is 81)")
    leaks = {
        "The answer is 81.": True,
        "It is eighty-one.": True,
        "Try option C.": True,
        "Correct: C": True,
        "Draw the square and mark the sides.": False,
        "Each side is 9 cm, so think about what to do next.": False,
        "Multiply two of the numbers.": False,
    }
    for hint, expected in leaks.items():
        found, reason = leak_check(hint, PROBLEM)
        check(f"{'catches' if expected else 'allows'}: {hint!r}", found == expected, f"got {found} {reason}")

    found, _ = leak_check("Think of a rhombus.", WORDY)
    check("catches a worded option value", found, "rhombus = E, so it leaks")
    found, _ = leak_check("A square has four equal sides.", WORDY)
    check("a different option's word is not a leak", not found, "square is option B")

    print("\nDiagnosis from the distractors")
    expected = {"A": "from_text", "B": None, "D": None, "E": None}
    for choice, tag in expected.items():
        got = diagnose(PROBLEM, choice)
        check(f"option {choice} -> {tag}", got["tag"] == tag, got["tag"])
        check(f"option {choice} never reveals the answer",
              "81" not in got["text"] and "C" not in got["text"], got["text"])
    check("half", diagnose(CANDY, "A")["tag"] in ("half", "one_step"), diagnose(CANDY, "A")["tag"])
    check("correct answer has no diagnosis", diagnose(PROBLEM, "C")["tag"] is None)

    def diag_case(correct, wrong):
        problem = {"id": "case", "language": "eng", "level": 1,
                   "text": "How many are there?", "options": {"A": wrong, "B": correct},
                   "label": "B"}
        return diagnose(problem, "A")["tag"]

    for expected_tag, (correct, wrong) in {
        "half": ("40", "20"),
        "double": ("40", "80"),
        "power_of_ten": ("40", "400"),
        "off_by_one": ("40", "41"),
        "digit_swap": ("81", "18"),
        "sign": ("40", "-40"),
        "unit": ("40 cm", "40 mm"),
    }.items():
        check(f"tag {expected_tag}", diag_case(correct, wrong) == expected_tag,
              diag_case(correct, wrong))
    check("no invented diagnosis", diag_case("40", "37") is None, diag_case("40", "37"))

    print("\nTopics")
    check("counting", topics.classify("In how many ways can three students sit?")["topic"] == "counting")
    check("geometry", topics.classify(PROBLEM["text"])["topic"] == "geometry")
    check("every/each is not automatically rate",
          topics.classify("Alicia has 6 triangular tiles. Between every two tiles she puts "
                          "a square, and each square gets a round tile.")["topic"] == "geometry")
    check("no substring trap: candies is not time",
          topics.classify("Marc has 9 candies and 17 sweets.")["topic"] != "time")

    print("\nCatalan, without a model")
    check("Catalan method", topics.classify(PROBLEM["text"], "cat")["method"].startswith("Geometria"))
    check("Catalan topic name", topics.display("geometry", "cat") == "geometria")
    check("Catalan diagnosis differs", diagnose(CANDY, "A", "cat")["text"]
          != diagnose(CANDY, "A", "eng")["text"])
    check("an English hint is recognised",
          looks_english("First add the numbers, then divide by the total."))
    check("a Catalan hint is kept",
          not looks_english("Primer suma els nombres i després divideix pel total."))

    print("\nA session, with no model and no index")
    tutor = FakeTutor(PROBLEM)
    session = Session(tutor)
    state = session.state()
    check("a problem is on screen", state["problem"] is not None)
    check("the label is not on the page", "label" not in (state["problem"] or {}))
    check("the first rung is offered", state["next_rung"] == 1)

    jumping = session.hint(3)
    check("skipping a rung is refused", "error" in jumping)

    hinted = session.hint()
    check("rung 1 arrives", hinted["hints"][-1]["rung"] == 1, hinted.get("error"))
    check("next rung is 2", hinted["next_rung"] == 2)
    session.hint(2)
    session.hint(3)
    check("three rungs, no model needed", len(session.state()["hints"]) == 3)
    check("no rung is offered after the third", session.state()["next_rung"] is None)
    solution_through_the_ladder = session.hint()
    check("the ladder cannot reveal the solution", "error" in solution_through_the_ladder,
          solution_through_the_ladder)

    answered = session.answer("A")
    check("wrong answer is marked wrong", answered["correct"] is False)
    check("answer is not revealed", answered["revealed"] is False
          and answered["revealed_label"] is None)
    check("a diagnosis is offered", answered["diagnosis"]["text"] != "")
    check("next is not ready until the problem is solved", answered["next_ready"] is False)
    check("the wrong letter is remembered",
          [attempt["choice"] for attempt in answered["attempts"]] == ["A"])
    twice = session.answer("A")
    check("retrying the same wrong letter is refused", "error" in twice)
    second = session.answer("B")
    check("another wrong letter is accepted",
          second["attempts"][-1]["choice"] == "B" and second["correct"] is False)
    solved = session.answer("C")
    check("the right letter is accepted", solved["correct"] is True)
    check("next is ready after the right letter", solved["next_ready"] is True)

    nxt = session.next()
    check("the next problem is different", nxt["problem"]["id"] != "area", nxt["problem"]["id"])
    check("the ladder restarts", nxt["hints"] == [] and nxt["answered"] is False)

    revealed = session.reveal()
    check("reveal is explicit", revealed["revealed"] is True and revealed["revealed_label"] == "C")
    check("the solution names the option", "C)" in (revealed["solution"] or ""))

    print("\nPrivate score and end of session")
    saved = session.history
    session.history = [{"points": 10}]
    check("a clean answer moves up", session._difficulty_delta(True) == 1)
    session.history = [{"points": 7}]
    check("hints hold the level", session._difficulty_delta(True) == 0)
    session.history = [{"points": -3}, {"points": 10}]
    check("one error holds the level", session._difficulty_delta(True) == 0)
    session.history = [{"points": -3}, {"points": -3}, {"points": 10}]
    check("repeated errors step down", session._difficulty_delta(True) == -1)
    session.history = saved
    finished = session.finish()
    check("finish closes the session", finished["finished"] is True)
    report = session.report or {}
    check("four feedback sections", len(report.get("feedback", [])) == 4)
    check("six charts", len(report.get("charts", [])) == 6)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("All checks passed.")
    return 0


class FakeTutor:
    """Just enough Tutor for a Session: two problems, an easier neighbour, no model."""

    def __init__(self, problem):
        from copilot.tutor import Profile
        self.problem = problem
        self.other = dict(CANDY)
        self.by_key = {f"{problem['id']}_{problem['language']}": problem,
                       f"{CANDY['id']}_{CANDY['language']}": CANDY}
        self.profile = Profile(os.path.join(os.path.dirname(__file__), "..", "results",
                                            "tutor-checks.db"))
        self.profile.reset()

    def public(self, problem, language=None):
        shown = {k: v for k, v in problem.items() if k != "label"}
        shown["key"] = f"{problem['id']}_{problem['language']}"
        shown["image"] = False
        shown["image_key"] = shown["key"]
        shown["image_language"] = problem["language"]
        shown["topic"] = topics.classify(problem["text"])["topic"]
        return shown

    def get(self, key):
        return self.by_key.get(key)

    def check(self, problem, choice, language="eng"):
        result = {"correct": choice == problem["label"], "label": problem["label"],
                  "label_value": problem["options"].get(problem["label"]),
                  "chosen_value": problem["options"].get(choice)}
        if not result["correct"]:
            result["diagnosis"] = diagnose(problem, choice, language)
        return result

    def hint(self, problem, rung, language="eng"):
        return {"rung": rung, "kind": "method", "text": "Try the method.", "status": "ok",
                "seconds": 0.0, "detail": {}}

    def reveal(self, problem, start=None, language="eng"):
        return {"rung": 4, "kind": "solution", "status": "ok", "seconds": 0.0,
                "text": f"The correct option is {problem['label']}) "
                        f"{problem['options'][problem['label']]}.",
                "detail": {}}

    def first_problem(self, level=0, language="eng", seed=42, topic=None):
        return self.problem

    def topic_counts(self, language="eng"):
        return [{"topic": "geometry", "count": 1}, {"topic": "arithmetic", "count": 1}]

    def next_problem(self, problem, correct, seen_ids=(), prefer_language=None, delta=None):
        other = CANDY if problem["id"] == "area" else PROBLEM
        return {"problem": self.public(other), "target_level": 0, "topic": "arithmetic",
                "reason": "test", "score": 0.0, "seconds": 0.0}


if __name__ == "__main__":
    sys.exit(main())
