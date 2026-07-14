from setuptools import setup, find_packages

setup(
    name="spec-echem",
    version="0.2.0",
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
