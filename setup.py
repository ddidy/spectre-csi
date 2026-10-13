#!/usr/bin/env python3
from setuptools import setup

setup(
    name="spectre-csi",
    version="1.0.0",
    description="Wi-Fi Channel State Information Presence & Motion Detector",
    author="Diddy",
    license="MIT",
    py_modules=["spectre_csi"],
    install_requires=[
        "numpy",
        "rich",
        "pyserial",
    ],
    entry_points={
        "console_scripts": [
            "spectre-csi=spectre_csi:main",
        ],
    },
    python_requires=">=3.8",
)
