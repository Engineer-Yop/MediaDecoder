"""Stands in for `claude -p` in tests: checks the arguments and stdin, replies like the real CLI."""
import json
import os
import sys

args = sys.argv[1:]
log_path = os.environ["FAKE_CLAUDE_LOG"]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "test@example.com"}))
    sys.exit(0)

line = sys.stdin.buffer.readline()
message = json.loads(line)
content = message["message"]["content"]
schema = json.loads(args[args.index("--json-schema") + 1])
with open(log_path, "w", encoding="utf-8") as f:
    json.dump({"args": [a if len(a) < 200 else a[:200] + "…" for a in args],
               "images": sum(1 for b in content if b["type"] == "image"),
               "env_has_api_key": "ANTHROPIC_API_KEY" in os.environ,
               "schema_keys": sorted(schema["properties"])}, f)

recipe = json.loads(open(os.environ["FAKE_CLAUDE_RECIPE"], encoding="utf-8").read())
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-opus-5-5"}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": json.dumps(recipe)}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "",
                  "structured_output": recipe,
                  "usage": {"input_tokens": 9000, "cache_read_input_tokens": 3345, "output_tokens": 987},
                  "modelUsage": {"claude-opus-5-5": {}}}), flush=True)
