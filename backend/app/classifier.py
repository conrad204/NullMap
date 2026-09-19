"""Auditable weak result labels and an optional trained linear embedding model.

Weak lexicon scores are heuristic scores, explicitly not calibrated probabilities.
Training consumes independently labelled embeddings and saves held-out metrics.
EvidenceInference is available at github.com/jayded/evidence-inference, but its
PICO-level directional labels require curation before abstract-level training.
"""

import json
import re
from pathlib import Path

import numpy as np

LABELS = ("positive", "null", "mixed", "no_result_stated")
WEAK_CLASSIFIER_VERSION = "weak-v2"
NULL_RE = re.compile(
    r"\b(?:no (?:statistically )?significant (?:between[- ]group )?"
    r"(?:differences?|effects?|benefits?|changes?|associations?|improvements?|reductions?)|"
    r"did not differ|not (?:statistically )?significan\w*|not superior|"
    r"no better than|"
    r"failed to (?:reach|show|demonstrate|improve|reduce|meet)|did not (?:reach|improve|reduce)|"
    r"futility|no evidence of (?:a |an )?(?:effect|benefit|difference|association)|"
    r"no meaningful (?:difference|effect|benefit))\b", re.IGNORECASE,
)
POSITIVE_RE = re.compile(
    r"\b(?:significantly (?:improved|reduced|increased|decreased|lower|higher)|"
    r"(?:statistically )?significant (?:improvement|reduction|increase|decrease|benefit)|"
    r"(?<!not )superior to)\b", re.IGNORECASE,
)
PROTOCOL_RE = re.compile(r"\b(?:study protocol|trial protocol|we (?:will|plan to)|results are (?:expected|pending))\b", re.IGNORECASE)


def weak_classify(abstract: str, *, is_review: bool = False) -> dict:
    sentences = [value.strip() for value in re.split(r"(?<=[.!?])\s+(?=[A-Z])", abstract) if value.strip()]
    nulls = [sentence for sentence in sentences if NULL_RE.search(sentence)]
    positives = []
    for sentence in sentences:
        # Strip only negated clauses, so 'no benefit ... but significantly reduced' remains mixed.
        cleaned = re.sub(r"\b(?:not|no)\s+(?:a\s+)?(?:statistically\s+)?significant\w*\s+\w+", "", sentence, flags=re.IGNORECASE)
        if POSITIVE_RE.search(cleaned):
            positives.append(sentence)
    if is_review or (PROTOCOL_RE.search(abstract) and not (nulls or positives)):
        label, score, span = "no_result_stated", 0.0, ""
    elif nulls and positives:
        label, score, span = "mixed", 0.5, nulls[-1]
    elif nulls:
        label, score, span = "null", 0.85, nulls[-1]
    elif positives:
        label, score, span = "positive", 0.1, positives[-1]
    else:
        label, score, span = "no_result_stated", 0.0, ""
    return {"result_label": label, "null_score": score, "evidence_span": span,
            "classification_method": "weak_lexicon", "null_score_is_probability": False,
            "weak_classifier_version": WEAK_CLASSIFIER_VERSION}


class ResultClassifier:
    def __init__(self, model_path: str | Path | None = None):
        self.model = json.loads(Path(model_path).read_text()) if model_path else None
        if self.model:
            if self.model.get("format") != "nullmap-linear-v1":
                raise ValueError("Unsupported classifier model format")
            if not set(self.model["classes"]).issubset(LABELS):
                raise ValueError("Classifier contains unsupported labels")

    def classify(self, study: dict) -> dict:
        result = weak_classify(study.get("abstract", ""), is_review=study.get("is_review", False))
        if not self.model or study.get("is_review"):
            return result
        if result["result_label"] == "mixed" and "mixed" not in self.model["classes"]:
            # Small training slices may lack enough mixed examples; preserve the auditable fallback.
            return result
        embedding = np.asarray(study.get("embedding", []), dtype=float)
        coefficients = np.asarray(self.model["coef"], dtype=float)
        if embedding.ndim != 1 or embedding.shape[0] != coefficients.shape[1]:
            raise ValueError("Study embedding dimensions do not match the classifier")
        if study.get("embedding_model") != self.model.get("embedding_model"):
            raise ValueError("Study embedding model does not match the classifier")
        logits = coefficients @ embedding + np.asarray(self.model["intercept"])
        classes = self.model["classes"]
        if len(classes) == 2 and len(logits) == 1:
            probability = 1 / (1 + np.exp(-np.clip(logits[0], -700, 700)))
            probabilities = np.array([1 - probability, probability])
        else:
            probabilities = np.exp(logits - logits.max())
            probabilities /= probabilities.sum()
        predicted = classes[int(probabilities.argmax())]
        if predicted != result["result_label"]:
            # A linear embedding model cannot supply a verbatim rationale by itself.
            result["evidence_span"] = ""
        result.update(result_label=predicted,
                      null_score=float(probabilities[classes.index("null")]) if "null" in classes else 0.0,
                      classification_method="trained_embedding_logistic",
                      null_score_is_probability=True)
        return result


def train_classifier(rows: list[dict], output: Path, *, label_field: str = "human_label",
                     test_fraction: float = 0.2, random_state: int = 42) -> dict:
    """Train on embeddings; human_label or independently sourced labels are required.

    Metrics are from a held-out stratified split, never reported as validated on
    humans if the provided labels were generated by an LLM or a weak labeller.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import classification_report, confusion_matrix
        from sklearn.model_selection import train_test_split
    except ImportError as exc:
        raise RuntimeError("Install the ingest extra to train the classifier") from exc
    if label_field == "result_label":
        raise ValueError("Use an independent label field, not the classifier's own result_label")
    if not 0 < test_fraction < 1:
        raise ValueError("test_fraction must be between zero and one")
    labels = [row.get(label_field) for row in rows]
    if any(label not in LABELS for label in labels):
        raise ValueError("Every training row must contain a supported reference label")
    classes, counts = np.unique(labels, return_counts=True)
    if len(classes) < 2 or min(counts) < 5:
        raise ValueError("Training requires at least two classes and five examples per class")
    models = {row.get("embedding_model") for row in rows}
    if len(models) != 1 or None in models:
        raise ValueError("Training rows must declare the same embedding_model")
    identifiers = [row.get("id") for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate study IDs would leak across train and test splits")
    vectors = np.asarray([row["embedding"] for row in rows], dtype=float)
    if vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError("Training embeddings must be a finite matrix")
    train_x, test_x, train_y, test_y = train_test_split(vectors, labels, test_size=test_fraction,
                                                      random_state=random_state, stratify=labels)
    model = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=random_state)
    model.fit(train_x, train_y)
    prediction = model.predict(test_x)
    metrics = {"label_field": label_field, "evaluation": "held_out_stratified",
               "train_count": len(train_y), "test_count": len(test_y), "random_state": random_state,
               "classification_report": classification_report(test_y, prediction, output_dict=True, zero_division=0),
               "confusion_matrix": confusion_matrix(test_y, prediction, labels=model.classes_).tolist(),
               "classes": model.classes_.tolist()}
    artifact = {"format": "nullmap-linear-v1", "classes": model.classes_.tolist(),
                "coef": model.coef_.tolist(), "intercept": model.intercept_.tolist(),
                "embedding_model": next(iter(models)), "metrics": metrics}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2))
    return metrics
