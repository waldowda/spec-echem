import re
from pathlib import Path

from setuptools import setup, find_packages

# Read the version out of the package rather than keeping a second copy here — importing
# spec_echem would drag in numpy et al. before they're installed, so match it as text.
_VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    Path(__file__).parent.joinpath("spec_echem", "build_info.py").read_text(encoding="utf-8"),
    re.M,
).group(1)

setup(
    name="spec-echem",
    version=_VERSION,
    author="Dean Waldow",
    description="Avantes spectrometer control for spectroelectrochemistry",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=[
        "numpy>=1.19.0",
        "scipy>=1.5.0",
        "matplotlib>=3.3.0",
        "pandas>=1.3.0",
    ],
    extras_require={
        # GUI deps (`pip install -e .[gui]`); the Qt binding isn't needed for the
        # library/notebook-only path. Vendor SDKs (avaspec, EchemToolkitPy) are not
        # pip-installable — see requirements.txt.
        "gui": ["PyQt5", "qtpy"],
    },
)
