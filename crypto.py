import os
import socket

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from framing import send_message, recv_message


class SecureChannel:
    """Wraps a socket. send() and recv() are encrypted automatically."""

    def __init__(self, sock: socket.socket, is_initiator: bool):
        self.sock = sock

        # 1. ephemeral keypair
        private = X25519PrivateKey.generate()
        my_public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

        # 2. swap public keys (32 bytes each)
        send_message(sock, my_public)
        peer_public = recv_message(sock)
        if len(peer_public) != 32:
            raise ValueError("bad public key")

        # 3. shared secret
        shared = private.exchange(X25519PublicKey.from_public_bytes(peer_public))

        # 4. derive two keys, one per direction
        #    the initiator is whoever dialed out; roles decide which key is which
        key_material = HKDF(
            algorithm=hashes.SHA256(),
            length=64,
            salt=None,
            info=b"luno v1 session keys",
        ).derive(shared)
        key_a, key_b = key_material[:32], key_material[32:]
        if is_initiator:
            send_key, recv_key = key_a, key_b
        else:
            send_key, recv_key = key_b, key_a

        self._send_cipher = ChaCha20Poly1305(send_key)
        self._recv_cipher = ChaCha20Poly1305(recv_key)
        self._send_counter = 0
        self._recv_counter = 0

    @staticmethod
    def _nonce(counter: int) -> bytes:
        return counter.to_bytes(12, "big")   # 12-byte nonce from a counter

    def send(self, plaintext: bytes) -> None:
        nonce = self._nonce(self._send_counter)
        self._send_counter += 1
        send_message(self.sock, self._send_cipher.encrypt(nonce, plaintext, None))

    def recv(self) -> bytes:
        ciphertext = recv_message(self.sock)
        nonce = self._nonce(self._recv_counter)
        self._recv_counter += 1
        return self._recv_cipher.decrypt(nonce, ciphertext, None)