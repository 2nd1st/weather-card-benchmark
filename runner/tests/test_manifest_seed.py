"""Golden tests for runner.manifest + runner.seed (COMPARISON-SCHEME-v12 §1.2,
data/SCHEMA/CONTRACT-NOTES §3/§5/§6).

Golden values are cross-checked two ways: (a) against a hand-built canonical byte
stream / byte layout that does NOT call the implementation under test, and (b)
against pinned literals so a change to BOTH the builder and the hand-built path
still trips.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from runner.manifest import (
    ManifestResult,
    build_manifest,
    canonical_decimal,
    compute_manifest_hash,
    nfc,
)
from runner.seed import (
    assign_config,
    bits_from_value,
    build_assignment_table,
    draw_assignment_rank,
    enumerate_omega,
    omega_size,
    seed_assign,
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "data" / "SCHEMA"

# --------------------------------------------------------------------------- #
# Fixed toy manifest
# --------------------------------------------------------------------------- #
TOY_KW = dict(
    prompt_min_sha256="a" * 64,
    prompt_q_sha256="b" * 64,
    snapshot_sha256="c" * 64,
    cities=[{"city": "Berlin", "date": "2026-07-16", "lat": "52.52", "lon": "13.405"}],
    config_ids=["cfg-b", "cfg-a"],  # deliberately unsorted -> must persist sorted
    N=3,
    env_digest="d" * 64,
    pipeline_commit="e" * 40,
)

# Hand-built JCS byte stream (key order = UTF-16 code-unit order: 'N'(0x4E) sorts
# before the lowercase keys; then cities < config_ids < env_digest <
# pipeline_commit < prompt_min < prompt_q < snapshot). config_ids sorted a<b.
EXPECTED_JCS = (
    '{"N":3,'
    '"cities":[{"city":"Berlin","date":"2026-07-16","lat":"52.52","lon":"13.405"}],'
    '"config_ids":["cfg-a","cfg-b"],'
    f'"env_digest":"{"d" * 64}",'
    f'"pipeline_commit":"{"e" * 40}",'
    f'"prompt_min_sha256":"{"a" * 64}",'
    f'"prompt_q_sha256":"{"b" * 64}",'
    f'"snapshot_sha256":"{"c" * 64}"}}'
).encode("utf-8")

# Pinned golden hash (independently == sha256(EXPECTED_JCS)).
EXPECTED_MANIFEST_HASH = "ce5e7b17e6d998b240f3c381de2bfa7929cdba34a1d0c8773eca7c69acde16f5"


def _build_toy() -> ManifestResult:
    return build_manifest(**TOY_KW)


# --------------------------------------------------------------------------- #
# manifest_hash golden
# --------------------------------------------------------------------------- #
def test_toy_manifest_jcs_bytes_match_hand_built():
    res = _build_toy()
    assert res.jcs_bytes == EXPECTED_JCS


def test_toy_manifest_hash_exact():
    res = _build_toy()
    assert res.manifest_hash == EXPECTED_MANIFEST_HASH
    # independent recompute from the hand-built byte stream
    assert res.manifest_hash == hashlib.sha256(EXPECTED_JCS).hexdigest()
    assert compute_manifest_hash(EXPECTED_JCS) == EXPECTED_MANIFEST_HASH


def test_config_ids_persisted_in_nfc_utf8_byte_order():
    res = _build_toy()
    assert res.manifest["config_ids"] == ["cfg-a", "cfg-b"]


def test_manifest_hash_is_lowercase_hex_64():
    res = _build_toy()
    assert len(res.manifest_hash) == 64
    assert res.manifest_hash == res.manifest_hash.lower()
    int(res.manifest_hash, 16)  # parses as hex


def test_round_trip_stability():
    res = _build_toy()
    reparsed = json.loads(res.jcs_bytes.decode("utf-8"))
    import rfc8785

    assert rfc8785.dumps(reparsed) == res.jcs_bytes


# --------------------------------------------------------------------------- #
# Schema validation of the toy manifest
# --------------------------------------------------------------------------- #
def test_toy_manifest_validates_against_schema():
    schema = json.loads((SCHEMA_DIR / "manifest.schema.json").read_text("utf-8"))
    Draft202012Validator.check_schema(schema)
    res = _build_toy()
    Draft202012Validator(schema).validate(res.manifest)


# --------------------------------------------------------------------------- #
# Canonical decimal grammar (CONTRACT-NOTES §3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0"),
        (52, "52"),
        ("52", "52"),
        ("52.52", "52.52"),
        ("13.405", "13.405"),
        ("-13.405", "-13.405"),
        ("-0.5", "-0.5"),
        ("05", "5"),  # normalized
        ("1.50", "1.5"),  # trailing zero stripped
        ("0.0", "0"),
        ("-0", "0"),  # no -0
        ("-0.0", "0"),
        ("52.520", "52.52"),
    ],
)
def test_canonical_decimal_normalizes(value, expected):
    assert canonical_decimal(value) == expected


@pytest.mark.parametrize("bad", ["1e5", "+5", "abc", ".5x", float("nan")])
def test_canonical_decimal_rejects(bad):
    with pytest.raises((ValueError, TypeError)):
        canonical_decimal(bad)


def test_float_coordinate_rejected():
    with pytest.raises(TypeError):
        canonical_decimal(52.52)


def test_integer_coordinate_no_fraction_form():
    # DECISIONS-M0 §2: integer coord serializes via grammar's no-fraction form.
    res = build_manifest(
        **{**TOY_KW, "cities": [{"city": "X", "date": "2026-07-16", "lat": 52, "lon": -1}]}
    )
    assert res.manifest["cities"][0]["lat"] == "52"
    assert res.manifest["cities"][0]["lon"] == "-1"


# --------------------------------------------------------------------------- #
# Dedup / NFC rules (CONTRACT-NOTES §2)
# --------------------------------------------------------------------------- #
def test_duplicate_config_id_after_nfc_rejected():
    # NFC-composed 'é' (U+00E9) vs decomposed 'e'+U+0301 are equal after NFC.
    composed = "café"
    decomposed = "café"
    assert nfc(composed) == nfc(decomposed)
    with pytest.raises(ValueError):
        build_manifest(**{**TOY_KW, "config_ids": [composed, decomposed]})


def test_duplicate_city_date_after_nfc_rejected():
    with pytest.raises(ValueError):
        build_manifest(
            **{
                **TOY_KW,
                "cities": [
                    {"city": "café", "date": "2026-07-16", "lat": "1", "lon": "2"},
                    {"city": "café", "date": "2026-07-16", "lat": "3", "lon": "4"},
                ],
            }
        )


def test_cities_sorted_by_element_jcs_byte_order():
    import rfc8785

    res = build_manifest(
        **{
            **TOY_KW,
            "cities": [
                {"city": "Zurich", "date": "2026-07-16", "lat": "47.37", "lon": "8.54"},
                {"city": "Berlin", "date": "2026-07-16", "lat": "52.52", "lon": "13.405"},
            ],
        }
    )
    keys = [rfc8785.dumps(c) for c in res.manifest["cities"]]
    assert keys == sorted(keys)
    assert res.manifest["cities"][0]["city"] == "Berlin"


def test_bad_N_rejected():
    for bad in (2, 6, 0):
        with pytest.raises(ValueError):
            build_manifest(**{**TOY_KW, "N": bad})


# --------------------------------------------------------------------------- #
# seed_assign golden (CONTRACT-NOTES §6, exact byte layout)
# --------------------------------------------------------------------------- #
FIXED_MH = "f" * 64


def _hand_seed(mh: str, cid: str, N: int) -> int:
    payload = (
        b"assign\x00"
        + bytes.fromhex(mh)
        + b"\x00"
        + unicodedata.normalize("NFC", cid).encode("utf-8")
        + b"\x00"
        + bytes([N])
    )
    return int.from_bytes(hashlib.sha256(payload).digest()[0:8], "little")


def test_seed_assign_exact_integer():
    # pinned literals, cross-checked against a hand-built byte layout
    assert seed_assign(FIXED_MH, "cfg-a", 3) == 2144430739494337403
    assert seed_assign(FIXED_MH, "cfg-a", 5) == 9296562941451149706
    assert seed_assign(EXPECTED_MANIFEST_HASH, "cfg-a", 3) == 15379371216725941581
    assert seed_assign(FIXED_MH, "cfg-a", 3) == _hand_seed(FIXED_MH, "cfg-a", 3)


def test_seed_assign_field_boundaries_no_collision():
    # \0 separators mean "a"+"bc" and "ab"+"c" must not collide.
    s1 = seed_assign(FIXED_MH, "a", 3)
    s2 = seed_assign(FIXED_MH, "\x00a", 3)  # different config_id bytes
    assert s1 != s2


def test_seed_assign_rejects_bad_manifest_hash():
    with pytest.raises(ValueError):
        seed_assign("F" * 64, "cfg-a", 3)  # uppercase
    with pytest.raises(ValueError):
        seed_assign("f" * 63, "cfg-a", 3)  # wrong length


# --------------------------------------------------------------------------- #
# Ω_c enumeration + rank encoding (CONTRACT-NOTES §6)
# --------------------------------------------------------------------------- #
def test_omega_sizes():
    # N=3: |n0-n1|<=1 -> popcount in {1,2} -> C(3,1)+C(3,2)=6
    assert omega_size(3) == 6
    # N=4: popcount==2 -> C(4,2)=6
    assert omega_size(4) == 6
    # N=5: popcount in {2,3} -> C(5,2)+C(5,3)=20
    assert omega_size(5) == 20


def test_omega_ascending_and_legal():
    for N in (3, 4, 5):
        omega = enumerate_omega(N)
        assert omega == sorted(omega)  # ascending == rank order
        assert len(set(omega)) == len(omega)
        for value in omega:
            n1 = bin(value).count("1")
            n0 = N - n1
            assert abs(n0 - n1) <= 1
        # completeness: every legal value present
        legal = [v for v in range(1 << N) if abs((N - bin(v).count("1")) - bin(v).count("1")) <= 1]
        assert omega == legal


def test_bits_msb_first():
    # value 0b100 with N=3 -> b[0]=MSB=1, b[1]=0, b[2]=0
    assert bits_from_value(0b100, 3) == [1, 0, 0]
    assert bits_from_value(0b011, 3) == [0, 1, 1]


def test_rank_round_trips_via_enumeration():
    omega = enumerate_omega(5)
    for rank, value in enumerate(omega):
        assert omega[rank] == value


# --------------------------------------------------------------------------- #
# Single per-config draw + assignment table (persisted OUTSIDE manifest)
# --------------------------------------------------------------------------- #
def test_draw_assignment_rank_in_range_and_deterministic():
    r1 = draw_assignment_rank(EXPECTED_MANIFEST_HASH, "cfg-a", 3)
    r2 = draw_assignment_rank(EXPECTED_MANIFEST_HASH, "cfg-a", 3)
    assert r1 == r2  # deterministic
    assert 0 <= r1 < omega_size(3)
    assert r1 == 2  # pinned golden from independent numpy recompute


def test_assign_config_golden():
    a = assign_config(EXPECTED_MANIFEST_HASH, "cfg-a", 3)
    assert a.seed_assign == 15379371216725941581
    assert a.omega_size == 6
    assert a.rank == 2
    assert a.omega_value == enumerate_omega(3)[2]  # == 3
    assert a.omega_value == 3
    assert a.bits == bits_from_value(3, 3) == [0, 1, 1]


def test_assignment_table_traversal_matches_persisted_order():
    res = _build_toy()
    order = res.manifest["config_ids"]  # ["cfg-a","cfg-b"]
    table = build_assignment_table(res.manifest_hash, order, res.manifest["N"])
    assert [row["config_id"] for row in table] == order
    # golden ranks (independent numpy recompute): cfg-a rank 2, cfg-b rank 4
    assert table[0]["rank"] == 2
    assert table[1]["rank"] == 4
    assert table[1]["omega_value"] == 5


def test_assignment_table_is_outside_manifest():
    # The table is a runtime artifact: none of its keys leak into the manifest.
    res = _build_toy()
    assert "assignment" not in res.manifest
    assert "seed_assign" not in res.manifest
    assert set(res.manifest) == {
        "prompt_min_sha256",
        "prompt_q_sha256",
        "snapshot_sha256",
        "cities",
        "config_ids",
        "N",
        "env_digest",
        "pipeline_commit",
    }
