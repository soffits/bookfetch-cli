# annas-archive-cli

A small command line tool for searching [Anna's Archive](https://en.wikipedia.org/wiki/Anna%27s_Archive) and downloading files through its member API. It has no dependencies beyond the Python standard library.

## You need a membership

Both search and download need an active Anna's Archive membership and its secret key (shown on your account page). Downloads go through the member fast-download API, and search pages sit behind a bot check that only lets members through when requests come from a script. Without a membership, expect search to fail with an HTTP error and downloads to return an API error.

## Install

With [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/soffits/annas-archive-cli
```

Or with pipx:

```bash
pipx install git+https://github.com/soffits/annas-archive-cli
```

Python 3.11 or newer is required.

## Usage

Give the tool your key, either directly or through a file:

```bash
export ANNAS_KEY_FILE=~/.config/annas/key   # a file that holds only the key
# or: export ANNAS_KEY=...
# or: annas --key-file ~/.config/annas/key ...
```

Search, then download by MD5:

```bash
annas search "pride and prejudice austen" --ext epub --lang en --limit 5
annas search "moby dick" --json
annas download 0123456789abcdef0123456789abcdef --out ~/Books
annas download 0123456789abcdef0123456789abcdef --out ~/Books --name moby-dick.epub
annas quota
```

`search` prints the title, author, publisher, a summary line (language, format, size, year) and the MD5 of each result. `download` prints the saved path and how many fast downloads you have left. `quota` shows your membership and how many fast downloads you have used recently.

Each `download` uses one of your fast downloads, even if the transfer fails afterwards, so pick the file before you download it.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `ANNAS_KEY` | The member secret key. |
| `ANNAS_KEY_FILE` | Path to a file containing the key. Used when `ANNAS_KEY` is not set. |
| `ANNAS_DOMAINS` | Comma-separated list of domains to try, in order. Overrides the built-in list. |

`--key-file` takes priority over both key variables.

## Use only official domains

There are many fake Anna's Archive mirrors, and some exist to collect member keys. The tool tries a built-in list of official domains in order. If those change, check the current list on [Wikipedia's Anna's Archive article](https://en.wikipedia.org/wiki/Anna%27s_Archive) before setting `ANNAS_DOMAINS`, and never type your key into a site that is not on it.

## Copyright

Copyright law differs from country to country. You are responsible for making sure that what you download is legal where you live.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

Tests run offline against small hand-made HTML fixtures.

## License

MIT
