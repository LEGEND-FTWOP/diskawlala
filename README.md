# Diskwala Video Player

A local Python server and browser player for Diskwala share links, direct video URLs, and local videos.

## Run or update

1. Download the latest ZIP from GitHub and extract it.
2. Stop the old server with Ctrl+C. Replace both `server.py` and `player.html`.
3. Run `python server.py` (Python 3.9 or newer; no extra Python packages).
4. Open http://127.0.0.1:8000 and refresh the page.
5. Paste a link and click **Resolve & Play**. **Download** stops playback and starts downloading, so the two transfers do not compete. Resolve the link again to resume playback afterward.

HLS playback loads HLS.js from a CDN.

## Streaming performance

The server races a small media sample from the source and HTTPS relay, selecting the first successful route. The other route remains available if opening the selected stream fails. If neither probe finishes within four seconds, the server tries the direct source first. Slow probe workers are bounded and do not delay route selection.

The proxy forwards available bytes immediately with `read1`, preserves byte ranges for seeking and download resumption, and supports concurrent requests. It no longer waits for optional filename metadata before resolving a video.

These changes remove avoidable local buffering and relay overhead. They cannot increase the upstream server's bandwidth. In a sample test from the development environment, the source delivered approximately 19 KiB/s and the relay 7 KiB/s over 256 KiB transfers. Actual speeds vary by network and time. If the source remains this slow, downloading before playback may be necessary.

## Limitations

Resolution depends on third-party services. Links can expire, require access through Diskwala, or return previews. Preview results are labelled in the player. Complete access to every link is not guaranteed. Browser codec support affects playback.

## Validation

Checked both supplied sample links for resolution and MP4 byte-range responses. Local integration checks cover faster-route selection, fallback after a source failure, exact seek bytes, download headers, concurrent requests, and immediate forwarding from a slow source. Chromium checks cover page startup, backend status, and releasing the playback connection when downloading. Full-video playback and speeds on the user's network were not verified.
