"""The one place the Omni harness version lives.

Scheme: V.R.U.P
  R  release  - 0 while in development; 1 is the first official GitHub
                release (V.1.0.0), and it moves only for official releases
  U  upgrade  - bumped for an upgrade (new feature or capability);
                the update number goes back to 0
  P  update   - bumped for an update (fix, tweak, small change)

Every merge bumps U or P depending on the kind of change. The title
banner, /version and /about all read VERSION from here.
"""

VERSION = "0.1.0"


def label() -> str:
    """The version as shown to the user: V.0.1.0"""
    return "V." + VERSION
