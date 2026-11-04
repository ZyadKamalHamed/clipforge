from clipforge.brain.llm.registry import KEYCHAIN_SERVICE
from clipforge.post import keychain


def test_keychain_round_trip_and_delete():
    ref = keychain.ref_for("a123")
    assert ref == "account:a123"
    assert keychain.get_token(ref) is None
    keychain.put_token(ref, "EAAfake-token")
    assert keychain.get_token(ref) == "EAAfake-token"
    keychain.delete_token(ref)
    assert keychain.get_token(ref) is None
    keychain.delete_token(ref)  # deleting what isn't there is fine


def test_keychain_uses_the_clipforge_service():
    import keyring

    keychain.put_token("account:a1", "secret")
    assert keychain.SERVICE == KEYCHAIN_SERVICE == "ClipForge"
    assert keyring.get_password("ClipForge", "account:a1") == "secret"


def test_tests_never_reach_the_real_keychain():
    import keyring

    assert type(keyring.get_keyring()).__name__ == "MemoryKeyring"
