Installation and usage
======================

Three recommended ways to install and run the tool.

1) Development / contributor (editable install)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .

# then run the installed console scripts
yt-video-query-tool-api
yt-video-query-tool-rss

# or run explicit paths
.venv/bin/yt-video-query-tool-api
.venv/bin/yt-video-query-tool-rss
```

2) End-user (isolated) — pipx

```bash
pipx install .
yt-video-query-tool-api
yt-video-query-tool-rss
```

3) Install from GitHub (non-editable)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install git+https://github.com/Sakib-Bin-Mahmud/yt-video-query-tool.git
yt-video-query-tool-api
```

Notes
- Avoid installing into system Python. Use `venv` or `pipx`.
- Console scripts are defined in `pyproject.toml` and install to the environment's `bin/`.
