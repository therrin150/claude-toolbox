#!/usr/bin/env python3
"""A stand-in for tasks.py in the publish tests: logs each call's arguments, one JSON list per line, to TASKS_LOG.
Set TASKS_FAIL to make every call fail."""

import json
import os
import sys

with open(os.environ["TASKS_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
if os.environ.get("TASKS_FAIL"):
    print("tasks.py: simulated failure", file=sys.stderr)
    sys.exit(1)
print("{}")
