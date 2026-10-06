from __future__ import annotations

import argparse
import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def generate(path: str) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )

    def encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    # Não sobrescreve chaves existentes nem expõe a chave privada no terminal.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(f"VAPID_PUBLIC_KEY={encode(public)}\nVAPID_PRIVATE_KEY={encode(private)}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera chaves VAPID em arquivo privado.")
    parser.add_argument("--output", required=True)
    generate(parser.parse_args().output)
