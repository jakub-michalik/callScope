"""Sphinx configuration for the CallScope documentation."""
import os
import sys

# autodoc imports the backend packages (engine, dsp, blocks, voip, …)
sys.path.insert(0, os.path.abspath(os.path.join("..", "backend")))

project = "CallScope"
author = "Jakub Michalik"
copyright = "2026, Jakub Michalik"
release = "0.2"
version = "0.2"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

# sounddevice needs system PortAudio at import time — mock it so docs build anywhere
autodoc_mock_imports = ["sounddevice"]
autodoc_member_order = "bysource"
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
napoleon_google_docstring = True
napoleon_numpy_docstring = False

templates_path = ["_templates"]
exclude_patterns = ["_build", "screenshots", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_title = "CallScope 0.2"
html_static_path = []

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}
