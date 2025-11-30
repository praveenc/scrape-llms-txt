# scrape-llms-txt

A robust Python 3.13 script for downloading markdown files referenced in llms.txt files.

## Features

- **PEP 723 Compliant**: Uses inline script metadata for dependency management
- **Async Downloads**: Concurrent file downloads for maximum efficiency
- **Progress Tracking**: Rich progress bars with download speeds and ETA
- **Structured Output**: Organizes downloads by date and domain: `downloads/MM-DD-YYYY/domain_name/<filename.md>`
- **Robust Error Handling**: Comprehensive error handling with detailed logging
- **Type Safe**: Uses Python 3.13 type aliases for better code clarity

## Requirements

- Python 3.13+
- `uv` or `pipx` for running PEP 723 scripts

## Installation

Install `uv` (recommended):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or use `pipx`:
```bash
pip install pipx
```

## Usage

Run the script with a URL or local file path to an llms.txt file:

```bash
# From a URL
uv run scrape-llms-txt.py https://www.fastht.ml/docs/llms.txt

# From a local file
uv run scrape-llms-txt.py ./sample-llms.txt
```

The script will:
1. Parse the llms.txt file
2. Extract all markdown file URLs
3. Download them concurrently with progress tracking
4. Save to `downloads/MM-DD-YYYY/domain_name/<filename.md>`

## Example Output

```
Starting llms.txt scraper...

Project: FastHTML

Downloading 5 files...

[████████████████████] quickstart_for_web_devs.html.md 45.2 KB 1.2 MB/s 0:00:00
[████████████████████] reference.md 23.1 KB 890 KB/s 0:00:00
...

✓ Successfully downloaded: 5
Files saved to: /path/to/downloads
```

## Development

Format and check code:
```bash
ruff format && ruff check --fix
```