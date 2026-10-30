import hashlib

import pytest

from ble_live import crypto


def h(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


def test_aes128_fips197_vectors():
    # FIPS-197 Appendix C.1
    assert crypto.aes128_encrypt_block(
        h("000102030405060708090a0b0c0d0e0f"), h("00112233445566778899aabbccddeeff")
    ) == h("69c4e0d86a7b0430d8cdb78070b4c55a")
    # FIPS-197 Appendix B
    assert crypto.aes128_encrypt_block(
        h("2b7e151628aed2a6abf7158809cf4f3c"), h("3243f6a8885a308d313198a2e0370734")
    ) == h("3925841d02dc09fbdc118597196a0b32")


def test_aes128_ctr_nist_sp800_38a_f5_1():
    key = h("2b7e151628aed2a6abf7158809cf4f3c")
    counter = h("f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff")
    plaintext = h("6bc1bee22e409f96e93d7e117393172a ae2d8a571e03ac9c9eb76fac45af8e51")
    ciphertext = h("874d6191b620e3261bef6864990db6ce 9806f66b7970fdff8617187bb9fffdff")
    assert crypto.aes128_ctr(key, counter, plaintext) == ciphertext
    assert crypto.aes128_ctr(key, counter, ciphertext) == plaintext
    # partial last block
    assert crypto.aes128_ctr(key, counter, plaintext[:20]) == ciphertext[:20]


def test_derive_key_matches_reference_formula():
    private = bytes(range(16))
    device = 0xEBC4B6B1
    expected = hashlib.sha256(private + bytes([0xB1, 0xB6, 0xC4, 0xEB])).digest()[:16]
    assert crypto.derive_key(private, device) == expected
    with pytest.raises(ValueError):
        crypto.derive_key(b"short", device)


def test_counter_block_and_payload_round_trip():
    assert crypto.counter_block(0x01020304) == bytes(range(1, 13)) + bytes([4, 3, 2, 1])
    key = crypto.derive_key(bytes(range(16)), 0x11223344)
    payload = bytes([0x07, 0x20, 0x00, 0x05, 0xA0, 0x00])
    sealed = crypto.crypt_payload(key, 7, payload)
    assert sealed != payload and len(sealed) == len(payload)
    assert crypto.crypt_payload(key, 7, sealed) == payload
    assert crypto.crypt_payload(key, 8, sealed) != payload  # counter matters
    # keystream is AES(key, nonce || counter) XOR payload, i.e. a single ECB block
    keystream = crypto.aes128_encrypt_block(key, crypto.counter_block(7))
    assert bytes(a ^ b for a, b in zip(payload, keystream, strict=False)) == sealed


def test_keyring():
    ring = crypto.KeyRing(private_key=bytes(range(16)))
    key = ring.key_for(5)
    assert key == crypto.derive_key(bytes(range(16)), 5)
    assert ring.decrypt(5, 1, crypto.crypt_payload(key, 1, b"\x07\x01")) == b"\x07\x01"
    empty = crypto.KeyRing()
    assert empty.key_for(5) is None and empty.decrypt(5, 1, b"\x00") is None
    empty.set_key(9, key)
    assert empty.key_for(9) == key
