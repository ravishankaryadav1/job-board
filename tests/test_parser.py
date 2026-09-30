import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from jobboard.common import BoardError
from jobboard.parser import FIELD_TYPES, OpenAIParser, validate_fields
from jobboard.store import Store


def fields():
    data = {name: {"value": None, "evidence": None} for name in FIELD_TYPES}
    data["role_type"] = {"value": "Internship", "evidence": "Biology internship"}
    return data


def response(data=None):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(data or fields())}]}]}


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.sqlite")
        self.sent = []

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def fake(self, payload, key):
        self.sent.append(deepcopy(payload))
        return response()

    def parser(self, **kwargs):
        return OpenAIParser(self.store, model="test-model", api_key="test-not-a-secret",
                            transport=kwargs.pop("transport", self.fake), **kwargs)

    def test_strict_schema_and_no_tools_or_storage(self):
        parser = self.parser()
        parser.parse("Biology internship", "12 weeks. Protein research.")
        payload = self.sent[0]
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("tools", payload)
        self.assertIn("untrusted source data", payload["input"][0]["content"])

    def test_unchanged_input_uses_cache(self):
        parser = self.parser()
        parser.parse("Biology internship", "12 weeks.")
        parser.parse("Biology internship", "12 weeks.")
        self.assertEqual((parser.calls, parser.cache_hits), (1, 1))

    def test_changed_input_and_model_invalidate_cache(self):
        parser = self.parser()
        parser.parse("Biology internship", "12 weeks.")
        parser.parse("Biology internship", "6 months.")
        parser.model = "another-model"
        parser.parse("Biology internship", "6 months.")
        self.assertEqual(parser.calls, 3)

    def test_budget_prevents_extra_request(self):
        parser = self.parser(max_calls=1)
        parser.parse("Biology internship", "12 weeks.")
        with self.assertRaisesRegex(BoardError, "budget"):
            parser.parse("Biology internship", "Changed source.")
        self.assertEqual(len(self.sent), 1)

    def test_hallucinated_evidence_rejected(self):
        data = fields(); data["degree"] = {"value": "PhD", "evidence": "PhD required"}
        with self.assertRaisesRegex(BoardError, "does not occur"):
            validate_fields(data, "Biology internship")

    def test_minimum_duration_not_short_term(self):
        data = fields()
        data["term_bucket"] = {"value": "Short-term (<=6 months)", "evidence": "at least 12 weeks"}
        with self.assertRaisesRegex(BoardError, "Minimum-only"):
            validate_fields(data, "Biology internship, at least 12 weeks")

    def test_refusal_and_incomplete_do_not_enter_cache(self):
        for result in [{"status": "incomplete"}, {"status": "completed", "output": [
            {"content": [{"type": "refusal", "refusal": "No"}]}]}]:
            parser = self.parser(transport=lambda p, k: result)
            with self.assertRaises(BoardError):
                parser.parse("Biology internship", "12 weeks.")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM parse_cache").fetchone()[0], 0)

    def test_unknown_fields_invalid_enum_and_missing_evidence(self):
        examples = [dict(fields(), extra="bad")]
        invalid = fields(); invalid["track"] = {"value": "Maybe", "evidence": None}; examples.append(invalid)
        invalid = fields(); invalid["degree"] = {"value": "PhD", "evidence": None}; examples.append(invalid)
        for data in examples:
            with self.assertRaises(BoardError):
                validate_fields(data, "Biology internship")

    def test_long_input_not_silently_truncated(self):
        parser = self.parser(max_chars=30)
        with self.assertRaisesRegex(BoardError, "input limit"):
            parser.parse("Biology internship", "X" * 50)
        self.assertEqual(parser.calls, 0)

    def test_minimum_acceptance_cannot_be_extracted_as_closing(self):
        data = fields()
        data["date_kind"] = {"value": "closing", "evidence": "accepted at least until October 3"}
        with self.assertRaisesRegex(BoardError, "Minimum acceptance"):
            validate_fields(data, "Biology internship; accepted at least until October 3")


if __name__ == "__main__":
    unittest.main()
