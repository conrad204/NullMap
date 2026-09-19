import json

import pytest

from app.classifier import ResultClassifier, weak_classify
from app.embeddings import Embedder


@pytest.mark.parametrize(("text", "label"), [
    ("The primary outcome did not differ between groups.", "null"),
    ("There was no significant improvement in cognition.", "null"),
    ("Symptoms significantly improved versus placebo.", "positive"),
    ("The primary outcome did not differ. Symptoms significantly improved.", "mixed"),
    ("This trial protocol describes how we will evaluate treatment.", "no_result_stated"),
    ("The treatment was not superior to placebo.", "null"),
    ("There were no significant differences between groups.", "null"),
    ("There were no statistically significant effects on cognition.", "null"),
    ("There were no significant between-group differences in change.", "null"),
    ("Outcomes after surgery were no better than those after a sham procedure.", "null"),
])
def test_weak_labels_and_verbatim_evidence(text, label):
    output = weak_classify(text)
    assert output["result_label"] == label
    assert output["evidence_span"] in text
    assert output["null_score_is_probability"] is False


def test_review_is_not_original_result():
    assert weak_classify("Treatment significantly improved outcomes.", is_review=True)["result_label"] == "no_result_stated"


def test_model_format_and_embedding_mismatch(tmp_path):
    artifact = {"format": "nullmap-linear-v1", "classes": ["null", "positive"],
                "coef": [[-1, 1]], "intercept": [0], "embedding_model": "test-model"}
    path = tmp_path / "classifier.json"
    path.write_text(json.dumps(artifact))
    classifier = ResultClassifier(path)
    result = classifier.classify({"abstract": "Results pending.", "embedding": [10, 0], "embedding_model": "test-model"})
    assert result["result_label"] == "null"
    assert result["null_score"] > 0.99
    assert result["evidence_span"] == ""
    with pytest.raises(ValueError, match="dimensions"):
        classifier.classify({"embedding": [1]})
    with pytest.raises(ValueError, match="model"):
        classifier.classify({"embedding": [1, 2], "embedding_model": "wrong"})


def test_embedder_is_lazy_and_empty_input_has_no_download():
    embedder = Embedder()
    assert embedder._model is None
    assert embedder.encode([]) == []
    assert embedder._model is None


def test_llm_labels_require_exact_evidence():
    from app.ingest.label import AbstractLabel, validate_label
    row = {"abstract": "The groups did not differ."}
    result = validate_label(AbstractLabel(label="null", evidence_span=row["abstract"]), row)
    assert result["llm_label"] == "null"
    assert result["label_is_human_reviewed"] is False
    with pytest.raises(ValueError, match="substring"):
        validate_label(AbstractLabel(label="null", evidence_span="Invented sentence."), row)


def test_materialization_checkpoint_changes_with_lexicon_version(tmp_path, monkeypatch):
    import asyncio

    import app.ingest.materialize as ingestion

    class Repository:
        index = "test"

        async def bulk_upsert(self, rows):
            pass

    source = tmp_path / "source.jsonl"
    source.write_text('{"id":"W1"}\n')
    asyncio.run(ingestion.materialize(source, Repository()))
    monkeypatch.setattr(ingestion, "WEAK_CLASSIFIER_VERSION", "future-test-version")
    with pytest.raises(ValueError, match="Source, target index or model changed"):
        asyncio.run(ingestion.materialize(source, Repository()))
