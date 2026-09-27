"""Deterministic synthetic value generation built on Faker.

Determinism contract
--------------------
For a fixed seed, a fixed set of tables generated in the same topological
order with the same row counts always produces the same values:

  * ``Faker.seed(seed)`` seeds Faker's global RNG;
  * a private ``random.Random(seed)`` backs every other random choice;
  * deterministic "code-like" columns (order numbers, emails local parts...)
    are built from the seed plus a monotonic sequence counter, so regenerating
    the same batch shape yields byte-identical strings and therefore collides
    with the previously committed rows on unique columns.

All generated names come from Faker's locale data and are **not real people**.
"""
from __future__ import annotations

import hashlib
import random
import re
from typing import Any

from faker import Faker


class GenerationError(Exception):
    """User-facing error with structured details for the UI."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValueGenerator:
    def __init__(self, seed: int, locale: str = "zh_CN"):
        self.seed = seed
        # Use a dedicated Faker instance seeded locally (not Faker.seed(),
        # which mutates process-global state and would let concurrent jobs
        # interfere with each other's reproducibility).
        self.faker = Faker(locale)
        self.faker.seed_instance(seed)
        self.rng = random.Random(seed)
        self._seq = 0

    # -- helpers ----------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _digest8(self, *parts: Any) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    def _clamp_text(self, value: Any, char_len: int | None) -> str:
        s = str(value)
        if char_len and len(s) > char_len:
            return s[:char_len]
        return s

    def _apply_check(
        self, col: str, value: Any, rules: list[dict[str, Any]]
    ) -> Any:
        for rule in rules:
            if rule["kind"] == "allowed_values":
                return self.rng.choice(rule["values"])
            if rule["kind"] == "range" and isinstance(value, (int, float)):
                op, bound = rule["op"], rule["value"]
                if op == ">=":
                    value = max(value, bound)
                elif op == ">":
                    value = max(value, bound + (1 if isinstance(value, int) else 0.0001))
                elif op == "<=":
                    value = min(value, bound)
                elif op == "<":
                    value = min(value, bound - (1 if isinstance(value, int) else 0.0001))
        return value

    # -- main entry -------------------------------------------------------

    def value_for(
        self,
        *,
        table: str,
        column: dict[str, Any],
        check_rules: list[dict[str, Any]] | None,
        unique_deterministic: bool = False,
        unique_seq: int | None = None,
    ) -> Any:
        """Generate one value for a non-FK, non-identity column."""
        name = column["name"].lower()
        udt = column["udt_name"].lower()
        full_type = column["full_type"].lower()
        char_len = column["char_len"]
        enum_values = column["enum_values"]

        if enum_values:
            return self.rng.choice(enum_values)

        if check_rules:
            allowed = next(
                (r for r in check_rules if r["kind"] == "allowed_values"), None
            )
            if allowed:
                return self.rng.choice(allowed["values"])

        # ---- text-ish types, dispatch by column name hint --------------
        if udt in ("varchar", "bpchar", "text", "char", "citext"):
            return self._text_value(
                table, name, char_len, unique_deterministic, unique_seq
            )

        # ---- numeric ----------------------------------------------------
        if udt in ("int2", "int4", "int8"):
            lo, hi = 1, 10_000
            if any(k in name for k in ("age",)):
                lo, hi = 18, 70
            elif any(k in name for k in ("qty", "quantity", "num", "count", "amount")):
                lo, hi = 1, 500
            value = self.rng.randint(lo, hi)
            return int(self._apply_check(name, value, check_rules or []))

        if udt in ("numeric", "numeric ", "money") or udt == "numeric":
            value = round(self.rng.uniform(1, 9999), 2)
            if char_len is None and column["num_scale"] == 0:
                value = int(value)
            return self._apply_check(name, value, check_rules or [])

        if udt in ("float4", "float8"):
            return round(self.rng.uniform(0, 1000), 2)

        if udt == "bool":
            return self.rng.choice([True, False])

        if udt == "date":
            return self.faker.date_between(start_date="-365d", end_date="today")

        if udt in ("timestamp", "timestamptz"):
            return self.faker.date_time_between(
                start_date="-180d", end_date="now"
            ).replace(microsecond=0)

        if udt == "time":
            return self.faker.time()

        if udt == "uuid":
            return self.faker.uuid4()

        if udt in ("json", "jsonb"):
            return {"generated": True, "seed": self.seed, "n": self._next_seq()}

        # fallback: textual rendering
        return self._text_value(
            table, name, char_len, unique_deterministic, unique_seq
        )

    # -- text dispatch ----------------------------------------------------

    def _text_value(
        self,
        table: str,
        name: str,
        char_len: int | None,
        unique_deterministic: bool,
        unique_seq: int | None,
    ) -> str:
        # Deterministic, collision-visible columns: regenerating with the
        # same seed produces exactly the same string.
        if unique_deterministic and unique_seq is not None:
            if "email" in name:
                v = f"seed{self.seed}.u{unique_seq:06d}@example.test"
            elif "phone" in name or "mobile" in name or "tel" in name:
                # 139 + 8 zero-padded digits => always a well-formed 11-digit number
                v = f"139{unique_seq % 100_000_000:08d}"
            elif "code" in name or "no" == name[-2:] or name.endswith("_no") or "number" in name:
                v = f"{table[:3].upper()}-S{self.seed}-{unique_seq:06d}"
            else:
                v = f"{table[:3].lower()}_{self.seed}_{unique_seq:06d}"
            return self._clamp_text(v, char_len)

        if "email" in name:
            return self._clamp_text(self.faker.unique.email(), char_len)
        if "phone" in name or "mobile" in name or "tel" in name:
            digits = re.sub(r"\D", "", self.faker.phone_number())
            if len(digits) >= 11:
                digits = digits[:11]
            return self._clamp_text(digits or "13800000000", char_len)
        if "name" in name and ("user" in name or "customer" in name or "person" in name
                               or "full" in name or name == "name" or name.endswith("_name")):
            return self._clamp_text(self.faker.name(), char_len)
        if "company" in name or "org" in name:
            return self._clamp_text(self.faker.company(), char_len)
        if "city" in name:
            return self._clamp_text(self.faker.city(), char_len)
        if "province" in name or "state" in name:
            return self._clamp_text(self.faker.province(), char_len)
        if "address" in name or "addr" in name:
            return self._clamp_text(
                self.faker.address().replace("\n", " "), char_len
            )
        if "zip" in name or "postcode" in name:
            return self._clamp_text(self.faker.postcode(), char_len)
        if "url" in name or "website" in name:
            return self._clamp_text(self.faker.url(), char_len)
        if "title" in name or "subject" in name:
            return self._clamp_text(self.faker.sentence(nb_words=4), char_len)
        if "remark" in name or "comment" in name or "note" in name or "desc" in name:
            return self._clamp_text(self.faker.sentence(nb_words=8), char_len)
        if "status" in name or "state" in name:
            return self._clamp_text(
                self.rng.choice(["active", "pending", "closed"]), char_len
            )
        if "currency" in name:
            return self._clamp_text(
                self.rng.choice(["CNY", "USD", "EUR"]), char_len
            )
        if "code" in name or name.endswith("_no") or "number" in name:
            v = f"{table[:3].upper()}-{self._digest8(self.seed, self._next_seq())}"
            return self._clamp_text(v, char_len)

        # generic fallback: non-real person name + suffix
        return self._clamp_text(
            f"{self.faker.name()}-{self._digest8(self.seed, self._next_seq())}",
            char_len,
        )

    # -- foreign keys -----------------------------------------------------

    def pick_parent(
        self, pool: list[tuple[Any, ...]]
    ) -> tuple[Any, ...]:
        """Choose one existing parent row's PK tuple for an FK."""
        if not pool:
            raise GenerationError("父表中没有可引用的记录")
        return self.rng.choice(pool)
