"""BLE Live encrypted packets (AD type 0xFD), as the reference design and receivers implement them.

The spec says only "AES-128 (CTR) with a shared private key". The reference design
(`ble_live.c`), GatewayKit and the WASP .NET library agree on the rest:

- key = SHA-256(private key (16 bytes) || device id (4 bytes, little-endian))[0:16]
- counter block = nonce (12 bytes, 01 02 .. 0C) || IV counter (4 bytes as sent on the air,
  little-endian after the device id)
- payload (flag byte and values, not the data identifier) XOR AES-128(key, counter block);
  payloads are at most 10 bytes so one block is enough, and no MAC is appended.

AES-128 is implemented here in plain Python (encrypt direction only, which is all CTR needs)
to avoid a native dependency; `tests/test_crypto.py` checks it against FIPS-197 and
NIST SP 800-38A vectors.
"""

from __future__ import annotations

import hashlib

DEFAULT_NONCE = bytes(range(1, 13))
KEY_BYTES = 16

# FIPS-197 S-box
_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76"
    "ca82c97dfa5947f0add4a2af9ca472c0"
    "b7fd9326363ff7cc34a5e5f171d83115"
    "04c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f84"
    "53d100ed20fcb15b6acbbe394a4c58cf"
    "d0efaafb434d338545f9027f503c9fa8"
    "51a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d1973"
    "60814fdc222a908846eeb814de5e0bdb"
    "e0323a0a4906245cc2d3ac629195e479"
    "e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a"
    "703eb5664803f60e613557b986c11d9e"
    "e1f8981169d98e949b1e87e9ce5528df"
    "8ca1890dbfe6426841992d0fb054bb16"
)
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a: int) -> int:
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _expand_key(key: bytes) -> list[bytes]:
    if len(key) != KEY_BYTES:
        raise ValueError("AES-128 key must be 16 bytes")
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 4 - 1]
        words.append([a ^ b for a, b in zip(words[i - 4], temp, strict=True)])
    return [bytes(sum(words[r * 4 : r * 4 + 4], [])) for r in range(11)]


def _encrypt_block(round_keys: list[bytes], block: bytes) -> bytes:
    state = [b ^ k for b, k in zip(block, round_keys[0], strict=True)]
    for rnd in range(1, 11):
        state = [_SBOX[b] for b in state]
        # ShiftRows on a column-major state: byte index = 4 * column + row
        state = [state[(r + 4 * ((c + r) % 4)) % 16] for c in range(4) for r in range(4)]
        if rnd != 10:
            mixed = []
            for c in range(4):
                a0, a1, a2, a3 = state[4 * c : 4 * c + 4]
                mixed += [
                    _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3,
                    a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3,
                    a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3),
                    (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3),
                ]
            state = mixed
        state = [b ^ k for b, k in zip(state, round_keys[rnd], strict=True)]
    return bytes(state)


def aes128_encrypt_block(key: bytes, block: bytes) -> bytes:
    if len(block) != 16:
        raise ValueError("block must be 16 bytes")
    return _encrypt_block(_expand_key(key), block)


def aes128_ctr(key: bytes, counter_block: bytes, data: bytes) -> bytes:
    """AES-128-CTR with the full 16-byte counter block incremented big-endian per block
    (mbedtls_aes_crypt_ctr semantics). Encrypt and decrypt are the same operation."""
    if len(counter_block) != 16:
        raise ValueError("counter block must be 16 bytes")
    round_keys = _expand_key(key)
    counter = int.from_bytes(counter_block, "big")
    out = bytearray()
    for offset in range(0, len(data), 16):
        keystream = _encrypt_block(round_keys, counter.to_bytes(16, "big"))
        chunk = data[offset : offset + 16]
        out += bytes(a ^ b for a, b in zip(chunk, keystream, strict=False))
        counter = (counter + 1) % (1 << 128)
    return bytes(out)


# -- BLE Live ---------------------------------------------------------------------------------


def derive_key(private_key: bytes, device_id: int) -> bytes:
    """SHA-256(private key || device id little-endian)[0:16]."""
    if len(private_key) != KEY_BYTES:
        raise ValueError("the shared private key is 16 bytes")
    digest = hashlib.sha256(private_key + device_id.to_bytes(4, "little")).digest()
    return digest[:KEY_BYTES]


def counter_block(iv_counter: int, nonce: bytes = DEFAULT_NONCE) -> bytes:
    if len(nonce) != 12:
        raise ValueError("nonce is 12 bytes")
    return nonce + (iv_counter & 0xFFFFFFFF).to_bytes(4, "little")


def crypt_payload(
    key: bytes, iv_counter: int, payload: bytes, nonce: bytes = DEFAULT_NONCE
) -> bytes:
    """Encrypt or decrypt a BLE Live payload (everything after the data identifier)."""
    return aes128_ctr(key, counter_block(iv_counter, nonce), payload)


class KeyRing:
    """Keys for decoding 0xFD frames: explicit per-device keys, or a shared private key from
    which a device's key is derived on first sight."""

    def __init__(self, private_key: bytes | None = None, nonce: bytes = DEFAULT_NONCE) -> None:
        self.private_key = private_key
        self.nonce = nonce
        self._keys: dict[int, bytes] = {}

    def set_key(self, device_id: int, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("AES-128 key must be 16 bytes")
        self._keys[device_id] = key

    @property
    def device_ids(self) -> list[int]:
        return sorted(self._keys)

    def key_for(self, device_id: int) -> bytes | None:
        key = self._keys.get(device_id)
        if key is None and self.private_key is not None:
            key = derive_key(self.private_key, device_id)
            self._keys[device_id] = key
        return key

    def decrypt(self, device_id: int, iv_counter: int, payload: bytes) -> bytes | None:
        key = self.key_for(device_id)
        if key is None:
            return None
        return crypt_payload(key, iv_counter, payload, self.nonce)
