import os
from pathlib import Path

import yaml


def test_render_dockerfile_paths_match_exact_case():
    root = Path(__file__).resolve().parents[1]
    blueprint = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))
    paths = [root / service["dockerfilePath"] for service in blueprint["services"] if "dockerfilePath" in service]
    assert paths
    for path in paths:
        assert path.name in os.listdir(path.parent), path
