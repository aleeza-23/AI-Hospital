"""List the model ids an OpenAI-compatible provider offers for your key.

    python scripts/list_models.py                                   # Groq (default)
    python scripts/list_models.py --base-url https://api.together.xyz/v1 --key-env TOGETHER_API_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medsim.config import load_env  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="https://api.groq.com/openai/v1")
    ap.add_argument("--key-env", default="GROQ_API_KEY")
    args = ap.parse_args()
    load_env()

    from openai import OpenAI

    key = os.environ.get(args.key_env)
    if not key:
        sys.exit(f"{args.key_env} is not set (add it to .env)")
    client = OpenAI(api_key=key, base_url=args.base_url)
    for m in sorted(client.models.list().data, key=lambda m: m.id):
        print(m.id)


if __name__ == "__main__":
    main()
