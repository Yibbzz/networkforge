"""Settings shared by the whole test suite."""

import os

from hypothesis import settings

# Hypothesis profiles, chosen with HYPOTHESIS_PROFILE (default "dev").
# Each example runs a full (tiny) network build, so no per-example deadline.
settings.register_profile("dev", max_examples=20, deadline=None)
settings.register_profile("ci", max_examples=50, deadline=None, print_blob=True)
settings.register_profile("thorough", max_examples=500, deadline=None, print_blob=True)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))
