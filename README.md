# Diskwala Video Player

A local Python server and browser player for Diskwala share links, direct video URLs, and local video files.

## Run

1. Install Python 3.
2. Download this repository using **Code → Download ZIP** and extract it.
3. Open a terminal in the extracted folder and run:

```sh
python server.py
```

4. Open http://127.0.0.1:8000 in your browser. Keep the terminal running.
5. Paste a link, click **Resolve & Play**, then use **Download** when available.

No Python packages are required. HLS playback loads HLS.js from a CDN.

## Changes

- Preserve the resolver's HTTPS relay instead of relying on its raw HTTP address on port 8080.
- Allow longer resolver timeouts.
- Handle concurrent playback and download requests.
- Download through the local server and forward byte ranges for seeking.
- Report resolver failures without assuming a file is protected.
- Show a notice when the resolver labels its result as a preview.

## Limitations

Diskwala resolution depends on external third-party services. Links may expire, stop working, require access through Diskwala, or return previews. Complete playback or downloading of every link is not guaranteed. Browser codec support also affects playback.

Both supplied sample links were checked for successful resolution, MP4 data, byte-range responses, and download headers. Full-video playback was not verified.
