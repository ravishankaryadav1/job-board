"""Optional evidence-backed Claude extraction via AWS Bedrock (Versa gateway).

Same parse(title, description) interface, prompt, schema and validation as
OpenAIParser in parser.py -- see docs/ARCHITECTURE.md "parser.py is the only
OpenAI integration... implement the same interface" for the extension contract
this follows. Only public posting text is sent. A model cannot change
availability or approve a row. The cache key includes the exact prompt, schema,
model and source text, same as the OpenAI path.
"""

import os

import anthropic

from .common import BoardError, digest
from .parser import PROMPT, SCHEMA, validate_fields

TOOL_NAME = "extract_job_fields"


def default_client(aws_access_key, aws_secret_key, aws_region, base_url):
    # max_retries=0: this project never auto-retries paid AI calls (see
    # OpenAIParser/api_request); the SDK default of 2 retries would silently
    # double- or triple-bill on a transient failure.
    return anthropic.AnthropicBedrock(
        aws_access_key=aws_access_key, aws_secret_key=aws_secret_key,
        aws_region=aws_region, base_url=base_url, max_retries=0,
    )


class ClaudeParser:
    def __init__(self, store, *, model=None, max_calls=20, max_chars=24000, client=None):
        self.store = store
        self.model = model or os.environ.get("CLAUDE_MODEL_ID")
        if not self.model:
            raise BoardError("Set CLAUDE_MODEL_ID in local .env to use --ai --provider claude")
        if client is not None:
            self.client = client
        else:
            # Standard boto3 env var names, not jobboard-specific ones, so this
            # lines up with whatever else in the environment already reads them
            # (e.g. an internal Bedrock gateway such as Versa).
            aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID")
            aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
            aws_region = os.environ.get("AWS_REGION")
            base_url = os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME")
            if not all((aws_access_key, aws_secret_key, aws_region, base_url)):
                raise BoardError(
                    "Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION and "
                    "AWS_ENDPOINT_URL_BEDROCK_RUNTIME in local .env to use --ai --provider claude")
            self.client = default_client(aws_access_key, aws_secret_key, aws_region, base_url)
        self.max_calls, self.max_chars = max_calls, max_chars
        self.calls = self.cache_hits = 0

    def parse(self, title, description):
        source = title + "\n\n" + description
        if len(source) > self.max_chars:
            raise BoardError("Posting exceeds parser input limit; review manually or increase limit in code")
        if not description.strip():
            raise BoardError("No description to parse")
        cache_key = digest({"provider": "claude", "model": self.model, "prompt": PROMPT,
                            "schema": SCHEMA, "source": source})
        cached = self.store.cache_get(cache_key)
        if cached:
            self.cache_hits += 1
            return validate_fields(cached, source)
        if self.calls >= self.max_calls:
            raise BoardError("AI call budget reached; remaining postings need manual review")
        self.calls += 1
        try:
            # output_config.format and strict tool use both return 400 ("Extra
            # inputs are not permitted") on this Bedrock deployment -- its
            # Anthropic API contract predates structured outputs. Plain forced
            # tool use works; validate_fields() below is the real schema guard.
            response = self.client.messages.create(
                model=self.model, max_tokens=6000, system=PROMPT,
                messages=[{"role": "user", "content": source}],
                tools=[{"name": TOOL_NAME, "description": "Return extracted job fields.",
                        "input_schema": SCHEMA}],
                tool_choice={"type": "tool", "name": TOOL_NAME},
            )
        except anthropic.AuthenticationError:
            raise BoardError("Claude authentication failed; verify AWS/Versa credentials locally") from None
        except anthropic.RateLimitError:
            raise BoardError("Claude rate limit hit; request was not retried") from None
        except anthropic.APIStatusError as exc:
            raise BoardError(
                f"Claude HTTP {exc.status_code}; verify credentials, model access or quota locally") from None
        except anthropic.APIConnectionError:
            raise BoardError("Claude transport failure; request was not retried") from None
        if response.stop_reason == "refusal":
            raise BoardError("Claude declined extraction; manual review required")
        data = next((block.input for block in response.content
                     if block.type == "tool_use" and block.name == TOOL_NAME), None)
        if data is None:
            raise BoardError("Claude response missing structured tool output")
        fields = validate_fields(data, source)
        self.store.cache_put(cache_key, fields)
        return fields
