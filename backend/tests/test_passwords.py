from app.auth import hash_password, verify_password


def test_password_hash_verifies_only_matching_password():
    encoded = hash_password("a-long-enough-test-password")

    assert verify_password("a-long-enough-test-password", encoded)
    assert not verify_password("another-password", encoded)


def test_password_verification_rejects_malformed_hash():
    assert not verify_password("password", "not-a-valid-hash")
