# ChemClaw Knowledge Graph desktop test builds

Desktop wrapper, resource assembly and Python freeze recipe adapted from [Zleap-AI/SAG v1.3.0](https://github.com/Zleap-AI/SAG/tree/v1.3.0/apps/desktop) (MIT). Existing 1.2.2 web/API and knowledge data model are retained. Original attribution and root MIT license remain applicable.

The desktop test workflow builds on native macOS ARM64 and Windows x64 runners. It uploads a DMG or EXE as a private Actions artifact, not a Release. macOS uses ad-hoc signing without notarization; Windows is unsigned. Automatic updates are intentionally disabled. The desktop icon is a temporary C mark, not a final company logo.

The backend binds to loopback (port 18100); the web process selects a free local port. The database, engine index and uploaded documents are stored below the OS application-data directory, in ChemClaw Knowledge Graph/.data. They are not included in installers. Do not overwrite or migrate an existing Docker database without a backup. Existing structured JSON import and graph UI are unchanged. Real GB-scale import, visualization and OS installation require manual acceptance testing.

CI builds the standalone Web server and frozen API, probes API readiness against a temporary empty database, then assembles the application runtime and packages the installer. Download from the successful Actions run's Artifacts section.
