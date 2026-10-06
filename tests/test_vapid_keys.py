import base64
import stat

import pytest
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid

from app.jobs.generate_vapid_keys import generate


def test_generated_keys_are_private_and_compatible_with_push_library(tmp_path):
    path = tmp_path / "vapid.env"
    generate(str(path))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    keys = dict(line.split("=", 1) for line in path.read_text().splitlines())
    signer = Vapid.from_string(keys["VAPID_PRIVATE_KEY"])
    public = signer.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert base64.urlsafe_b64encode(public).decode().rstrip("=") == keys["VAPID_PUBLIC_KEY"]
    with pytest.raises(FileExistsError):
        generate(str(path))
    assert dict(line.split("=", 1) for line in path.read_text().splitlines()) == keys
