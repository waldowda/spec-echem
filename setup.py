from setuptools import setup, find_packages

setup(
    name="spec-echem",
    version="0.1.0",
    author="Dean Waldow",
    description="Avantes spectrometer control for spectroelectrochemistry",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=[
        "numpy>=1.19.0",
        "matplotlib>=3.3.0",
        "pandas>=1.3.0",
    ],
)
