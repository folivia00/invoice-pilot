"""Shared test configuration: Hypothesis profiles.

The default profile ("ci") is deterministic (derandomize=True) and uses no
example database, so CI and local runs explore the same examples and leave no
.hypothesis/ directory behind. Select another profile with
HYPOTHESIS_PROFILE=dev|thorough for randomized exploration.
"""

import os

from hypothesis import settings

settings.register_profile("ci", derandomize=True, max_examples=200, deadline=None, database=None)
settings.register_profile("dev", max_examples=100, deadline=None)
settings.register_profile("thorough", max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
