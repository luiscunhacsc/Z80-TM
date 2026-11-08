"""Fetch a single vector from pinned files too large for the connector API."""
import concurrent.futures
import json
from pathlib import Path
import urllib.parse
import urllib.request

COMMIT = "ebe1875d48f374bcfd4b505d8eb8ee751568b5f7"


def fetch(item):
    path, blob = item
    url = f"https://raw.githubusercontent.com/SingleStepTests/z80/{COMMIT}/" + urllib.parse.quote(path)
    request = urllib.request.Request(url, headers={"Range": "bytes=0-32767", "User-Agent": "z80-tm-fixture-import"})
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read(32768).decode("utf-8")
    vector, _ = json.JSONDecoder().raw_decode(text, text.index("{"))
    return {"opcode": path[3:-5], "blob": blob, "tests": [vector]}


def main():
    destination = Path("tests/fixtures/prefixes.json")
    data = json.loads(destination.read_text(encoding="utf-8"))
    present = {"v1/" + f["opcode"] + ".json" for f in data["fixtures"]}
    index = json.loads(Path("tools/fixture-index.json").read_text(encoding="utf-8"))
    missing = [(p, blob) for p, blob in index.items() if p not in present and " " in p]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for number, fixture in enumerate(pool.map(fetch, missing), 1):
            data["fixtures"].append(fixture)
            if number % 50 == 0:
                print(f"Fetched {number}/{len(missing)} large fixture files", flush=True)
    data["fixtures"].sort(key=lambda f: f["opcode"])
    destination.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Saved {len(data['fixtures'])} prefixed encodings", flush=True)


if __name__ == "__main__":
    main()
