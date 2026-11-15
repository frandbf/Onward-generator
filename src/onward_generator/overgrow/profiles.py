"""Year profiles scale density. They are not a growth simulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Profile:
    years: int
    grass: float
    trees: float
    wrecks: float
    props: float
    vines: float
    bushes: float


# The years scale what grows and what rusts, not the street props: since 14 November 2026 every profile keeps their
# full rule caps (``props`` 1.0, was 0.35 and 0.65). A cut by years left a recently abandoned street half empty with
# most of its triangle budget unspent (seen on Murcia Catedral: 29 props, 77 000 triangles left). The triangle headroom
# (``place.triangle_headroom``) and ``MAX_LIBRARY_TRIS`` bound the props instead, the same way on every map.
PROFILES = {
    10: Profile(10, 0.20, 0.15, 0.25, 1.0, 0.15, 0.20),
    30: Profile(30, 0.50, 0.45, 0.55, 1.0, 0.45, 0.50),
    100: Profile(100, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
}


def profile_for(years: int) -> Profile:
    if years not in PROFILES:
        raise ValueError("profile must be 10, 30 or 100 years")
    return PROFILES[years]


def scaled_cap(base: int, factor: float) -> int:
    if factor <= 0 or base <= 0:
        return 0
    return max(1, int(round(base * factor)))


def rule_factor(profile: Profile, category: str, roles: tuple[str, ...]) -> float:
    if category == "vegetation" and "tree" in roles:
        return profile.trees
    if category == "vegetation" and "bush" in roles:
        return profile.bushes
    if category == "vegetation":
        return profile.grass
    return profile.props
