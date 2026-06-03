Release notes
=============

Full history: `CHANGELOG.md <https://github.com/jakub-michalik/callScope/blob/main/CHANGELOG.md>`_ ·
`GitHub releases <https://github.com/jakub-michalik/callScope/releases>`_.

v0.7.2
------
- **Investigating problems** guide (this section's :doc:`troubleshooting` page): the fault-analysis
  workflow and a common-problems table.

v0.7.1
------
- **Dockerized baresip backend** — the ``live`` backend runs a real ``baresip`` from Docker; the
  adapter auto-connects to its ``ctrl_tcp`` on ``127.0.0.1:4444``.
- Patchbay relabels the gateway as **Asterisk** in live mode.

v0.7
----
- Docs grouped into **Architecture** and **Reference** with nested contents.

v0.6.1 / v0.6.2
---------------
- Hosted documentation (autodoc API, Mermaid diagrams, screenshots, version switcher);
  GitHub Actions CI + docs deploy.

v0.2
----
- First tagged release: native SIP/RTP stack (UAC + UAS), three backends, real audio,
  root-cause correlator, 83 tests.
