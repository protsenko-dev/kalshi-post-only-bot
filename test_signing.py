"""Offline proof of the signing recipe. Generates a throwaway RSA key, signs the way kalshi_client does,
verifies with the public key, and shows that a different method or path fails verification."""
import base64
import os
import tempfile
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_client import KalshiClient, KalshiCredentials


def main() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    with tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False) as f:
        f.write(pem)
        path = f.name
    try:
        client = KalshiClient(KalshiCredentials(key_id="test-key", private_key_path=path), demo=True)
        ts = str(int(time.time() * 1000))
        method, full_path = "POST", "/trade-api/v2/portfolio/events/orders"
        sig = base64.b64decode(client._sign(ts, method, full_path))
        pub = key.public_key()
        pss = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size)
        pub.verify(sig, f"{ts}{method}{full_path}".encode(), pss, hashes.SHA256())
        print("signature verifies for", method, full_path)
        for bad in ((ts, "GET", full_path), (ts, method, "/trade-api/v2/markets")):
            try:
                pub.verify(sig, f"{bad[0]}{bad[1]}{bad[2]}".encode(), pss, hashes.SHA256())
                print("UNEXPECTED: verified", bad)
            except Exception:
                print("correctly rejected", bad[1], bad[2])
        h = client._headers("GET", "/trade-api/v2/markets")
        assert set(h) >= {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
        print("headers present:", sorted(k for k in h if k.startswith("KALSHI")))
    finally:
        os.remove(path)


if __name__ == "__main__":
    main()
