"""The toolbox repo's own layout: the old folders are gone, and every feature folder is well formed.

Run from the repo root:

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core" / "workspace" / ".claude" / "toolbox" / "lib"))

import config  # noqa: E402


def specs() -> list:
    return [(folder, config.read_feature(yaml.safe_load((folder / "feature.yml").read_text(encoding="utf-8")),
                                         config.feature_name(REPO, folder)))
            for folder in config.feature_folders(REPO)]


class RepoLayout(unittest.TestCase):
    def test_the_old_layout_is_gone(self):
        for path in ("workspace", "global", "workspace/features.yml", "core/feature.yml", "core/settings.json"):
            self.assertFalse((REPO / path).exists(), path)

    def test_core_has_a_global_and_a_workspace_folder(self):
        self.assertEqual([folder.relative_to(REPO).as_posix() for folder in config.core_folders(REPO)],
                         ["core/global", "core/workspace"])

    def test_every_feature_folder_reads_and_installs_only_known_roots(self):
        found = specs()
        self.assertGreater(len(found), 20)
        for folder, spec in found:
            self.assertEqual(sorted(entry.name for entry in folder.iterdir()
                                    if entry.name not in {".DS_Store", "__pycache__"}
                                    and entry.name not in {"feature.yml", "settings.json", ".claude", ".vault"}), [],
                             f"{folder.name}: something besides feature.yml, settings.json, .claude and .vault")
            installs = [folder / ".claude", folder / ".vault", folder / "settings.json"]
            self.assertTrue(any(path.exists() for path in installs), f"{folder.name} installs nothing")

    def test_requires_name_features_of_this_repo_and_needs_are_config_keys(self):
        found = specs()
        names = {spec["name"] for _, spec in found}
        for _, spec in found:
            for need in spec["requires"]:
                self.assertIn(need, names, f"{spec['name']} requires {need}")
            for key in spec["needs"]:
                self.assertTrue(config.schema_has(key), f"{spec['name']} needs {key}")

    def test_feature_names_are_unique_within_a_scope(self):
        seen = set()
        for folder, spec in specs():
            key = (spec["name"], spec["target"])
            self.assertNotIn(key, seen, folder)
            seen.add(key)
        workspace = {name for name, target in seen if target == "workspace"}
        self.assertFalse(workspace & {name for name, target in seen if target == "global"} - {"core"})

    def test_every_global_feature_is_in_the_machine_example(self):
        listed = set(re.findall(r"^#\s+- (\S+)$", (REPO / "machine.example.yml").read_text(encoding="utf-8"), re.M))
        wanted = {spec["name"] for _, spec in specs() if spec["target"] == "global" and spec["name"] != "core"}
        self.assertEqual(wanted - listed, set())

    def test_every_workspace_feature_is_in_the_readme(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for _, spec in specs():
            if spec["name"] != "core":
                self.assertIn(f"| `{spec['name']}` |", readme, spec["name"])

    def test_scripts_find_the_repo_from_their_place_in_a_feature_folder(self):
        for feature, skill in (("setup-workspace", "setup-workspace"), ("ship-toolbox-change", "ship-toolbox-change")):
            script = next((REPO / "features" / feature / ".claude" / "skills" / skill / "scripts").glob("*.py"))
            self.assertEqual(script.resolve().parents[6], REPO)


if __name__ == "__main__":
    unittest.main()
