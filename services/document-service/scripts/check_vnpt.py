"""Opt-in probe of the supplied Java endpoint with a synthetic image. No real CCCD.

This sends one provider request and may consume quota; never prints provider JSON.
"""
import argparse
import io
from pathlib import Path
import sys
import time

from dotenv import load_dotenv
from PIL import Image

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from app.core.config import ConfigurationError
from app.core.errors import DocumentError
from app.services.identity_images import prepare_identity_image
from app.services.vnpt import Vnpt, VnptSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-probe", action="store_true")
    args = parser.parse_args()
    load_dotenv(SERVICE.parents[1] / ".env", override=False)
    try:
        settings = VnptSettings.from_env()
        print("PASS: VNPT settings validated locally; token values hidden. Credentials not verified.")
        if not args.run_probe:
            print("No network request. Use --run-probe to send one synthetic image.")
            return 0
        with io.BytesIO() as output, Image.new("RGB", (32, 20), "white") as img:
            img.save(output, format="PNG")
            image = prepare_identity_image("front", "synthetic.png", "image/png", output.getvalue())
        Vnpt(settings).read_image(image, deadline=time.monotonic()+25, on_response=lambda _: None)
        print("Provider accepted a synthetic image with sample-shaped fields; NOT an acceptance test for real CCCD.")
    except (ConfigurationError, DocumentError) as exc:
        print(str(exc) if isinstance(exc, ConfigurationError) else exc.code + ": " + exc.message)
        if isinstance(exc, DocumentError) and exc.details:
            print("Provider HTTP status:", exc.details.get("provider_http_status"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
