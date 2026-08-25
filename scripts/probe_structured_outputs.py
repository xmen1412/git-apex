import json
import os

from openai import OpenAI

client = OpenAI(api_key=os.environ["LLM_API_KEY"], base_url=os.environ["LLM_BASE_URL"])

schema = {
    "type": "object",
    "properties": {
        "route": {"type": "string", "enum": ["relational", "analytical", "semantic", "chained"]},
        "intent": {"type": "string"},
    },
    "required": ["route", "intent"],
    "additionalProperties": False,
}

try:
    r = client.chat.completions.create(
        model=os.environ["LLM_MODEL"],
        messages=[{"role": "user", "content": "who changed auth.py?"}],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "route", "schema": schema, "strict": True},
        },
    )
    print("SUPPORTED:", r.choices[0].message.content)
except Exception as e:
    print("NOT SUPPORTED:", type(e).__name__, e)
