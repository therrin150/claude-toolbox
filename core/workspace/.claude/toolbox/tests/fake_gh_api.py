#!/usr/bin/env python3
"""A stand-in for `gh` in the setup-workspace and doctor tests: only `auth status` and `gh api` reads.

Environment (set by the tests):
  GH_FAKE_LOGIN       the signed-in account; unset, `auth status` fails as if signed out
  GH_FAKE_PROTECTION  JSON map of "<owner>/<repo>/<branch>" -> the branch protection response; any other branch is
                      unprotected (HTTP 404, and no rulesets)
"""

import json
import os
import sys


def main() -> int:
    args = sys.argv[1:]
    login = os.environ.get("GH_FAKE_LOGIN")
    if args[:2] == ["auth", "status"]:
        if not login:
            print("You are not logged into any GitHub hosts. To log in, run: gh auth login", file=sys.stderr)
            return 1
        print(f"Logged in to github.com account {login}")
        return 0
    if args[:1] != ["api"] or not login:
        print("unsupported or signed out", args, file=sys.stderr)
        return 1
    path = args[1]
    if path == "user":
        print(login if "--jq" in args else json.dumps({"login": login}))
        return 0
    protection = json.loads(os.environ.get("GH_FAKE_PROTECTION") or "{}")
    if path.endswith("/protection"):
        key = path.removeprefix("repos/").replace("/branches/", "/").removesuffix("/protection")
        if key in protection:
            print(json.dumps(protection[key]))
            return 0
        print("gh: Branch not protected (HTTP 404)", file=sys.stderr)
        return 1
    if "/rules/branches/" in path:
        print("[]")
        return 0
    print(f"gh: Not Found (HTTP 404): {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
