"""Turn the model's five scores into one answer, and say how sure it is.

A small model has habits: it picks D and A far more often than the real answer
key does. We can measure those habits on the training set and lean against them.
With alpha=0 this file does exactly what the baseline did (the top letter wins),
so it is safe to ship even if the calibration turns out not to help.
"""
import json
import math
import os

from .config import ROOT

LETTERS = "ABCDE"
DEFAULT_PRIOR = {letter: 0.2 for letter in LETTERS}
_TRAIN = os.path.join(ROOT, "data", "train.jsonl")
_prior_cache = None


def label_prior(path=_TRAIN, smoothing=1.0):
    """How often each letter is the answer in the training set (smoothed)."""
    global _prior_cache
    if path == _TRAIN and _prior_cache is not None:
        return _prior_cache
    counts = {letter: 0.0 for letter in LETTERS}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                label = (json.loads(line).get("label") or "").upper()
                if label in counts:
                    counts[label] += 1
    except OSError:
        return dict(DEFAULT_PRIOR)
    total = sum(counts.values())
    if total == 0:
        return dict(DEFAULT_PRIOR)
    prior = {letter: (counts[letter] + smoothing) / (total + smoothing * len(LETTERS))
             for letter in LETTERS}
    if path == _TRAIN:
        _prior_cache = prior
    return prior


def probabilities(scores):
    """Log-probabilities -> probabilities that add up to one."""
    if not scores:
        return {}
    best = max(scores.values())
    weights = {letter: math.exp(score - best) for letter, score in scores.items()}
    total = sum(weights.values())
    return {letter: weight / total for letter, weight in weights.items()}


def choose(scores, fallback="D", prior=None, alpha=0.0):
    """Pick a letter. alpha=0 trusts the model; alpha=1 fully removes its bias."""
    if not scores:
        return fallback
    adjusted = dict(scores)
    if alpha:
        prior = prior or label_prior()
        for letter in list(adjusted):
            adjusted[letter] -= alpha * math.log(prior.get(letter, 1 / len(LETTERS)))
    return max(adjusted, key=adjusted.get)


def confidence(scores, answer):
    """The model's probability for the letter it actually gave (0 to 1)."""
    return probabilities(scores).get(answer, 0.0)


def compact(scores):
    """A short text version for the results CSV, e.g. 'A:-0.1,D:-2.3'."""
    return ",".join(f"{letter}:{scores[letter]:.2f}" for letter in LETTERS if letter in scores)
