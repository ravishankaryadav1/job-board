import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jobboard.common import BoardError
from jobboard.parser import SCHEMA

try:
    from jobboard.claude_parser import ClaudeParser
except ImportError:
    ClaudeParser = None

from test_parser import fields


class FakeMessages:
    def __init__(self, handler):
        self.handler = handler

    def create(self, **kwargs):
        return self.handler(kwargs)


class FakeClient:
    def __init__(self, handler):
        self.messages = FakeMessages(handler)


def response(data=None, stop_reason="tool_use", name="extract_job_fields"):
    return SimpleNamespace(stop_reason=stop_reason, content=[
        SimpleNamespace(type="tool_use", name=name, input=data if data is not None else fields())])


@unittest.skipUnless(ClaudeParser, "anthropic[bedrock] is an optional extra; not installed")
class ClaudeParserTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        from jobboard.store import Store
        self.store = Store(Path(self.temp.name) / "state.sqlite")
        self.sent = []

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def handler(self, kwargs):
        self.sent.append(deepcopy(kwargs))
        return response()

    def parser(self, **kwargs):
        handler = kwargs.pop("handler", self.handler)
        return ClaudeParser(self.store, model="test-model", client=FakeClient(handler), **kwargs)

    def test_forces_the_extraction_tool(self):
        parser = self.parser()
        parser.parse("Biology internship", "12 weeks. Protein research.")
        payload = self.sent[0]
        self.assertEqual(payload["tools"][0]["input_schema"], SCHEMA)
        self.assertEqual(payload["tool_choice"], {"type": "tool", "name": "extract_job_fields"})
        self.assertIn("untrusted source data", payload["system"])

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

    def test_refusal_does_not_enter_cache(self):
        parser = self.parser(handler=lambda k: response(stop_reason="refusal"))
        with self.assertRaisesRegex(BoardError, "declined"):
            parser.parse("Biology internship", "12 weeks.")
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM parse_cache").fetchone()[0], 0)

    def test_long_input_not_silently_truncated(self):
        parser = self.parser(max_chars=30)
        with self.assertRaisesRegex(BoardError, "input limit"):
            parser.parse("Biology internship", "X" * 50)
        self.assertEqual(parser.calls, 0)

    def test_missing_credentials_reported(self):
        blanked = {k: "" for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                                    "AWS_REGION", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME")}
        with patch.dict(os.environ, blanked), self.assertRaisesRegex(BoardError, "AWS_ACCESS_KEY_ID"):
            ClaudeParser(self.store, model="test-model")


if __name__ == "__main__":
    unittest.main()
