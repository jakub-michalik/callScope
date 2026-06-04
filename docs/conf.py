"""Sphinx configuration for the CallScope documentation."""
import os
import sys

# autodoc imports the backend packages (engine, dsp, blocks, voip, …)
sys.path.insert(0, os.path.abspath(os.path.join("..", "backend")))

project = "CallScope"
author = "Jakub Michalik"
copyright = "2026, Jakub Michalik"
release = "0.7.7"
version = "0.7.7"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinxcontrib.mermaid",
]

# render Mermaid as inline SVG (no client-side JS needed to view the diagrams)
mermaid_output_format = "raw"

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
html_title = "CallScope"
html_static_path = ["_static"]
html_logo = "_static/logo.svg"
html_favicon = "_static/logo.svg"
html_js_files = ["versions.js"]
html_css_files = ["custom.css"]

# add a version/release switcher to the furo sidebar (populated from versions.json)
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "version-switcher.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}
