import http.server
import socketserver
import json
import urllib.request
import urllib.parse
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
import urllib.error

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

def probe_stream(url):
    """Bounded range check: reject HTML/error pages and measure actual media delivery."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-16383',
        'Accept-Encoding': 'identity'})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=3) as resp:
        kind = resp.headers.get('Content-Type', '').lower()
        if resp.status not in (200, 206) or any(t in kind for t in ('text/html', 'application/json')):
            raise ValueError('Source did not return media')
        # Probe needs real bytes, not just fast response headers.
        data = bytearray()
        while len(data) < 16384:
            if time.monotonic() - started > 3:
                raise TimeoutError('Media sample too slow')
            chunk = resp.read1(16384 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        if not data:
            raise ValueError('Empty stream')
        total = resp.headers.get('Content-Range', '').rsplit('/', 1)[-1]
        size = int(total) if total.isdigit() else 0
        return url, size


def choose_stream(candidates):
    """Race a small sample from source and relay; never wait on the slow loser."""
    candidates = list(dict.fromkeys(candidates))
    pool = ThreadPoolExecutor(max_workers=len(candidates))
    futures = [pool.submit(probe_stream, url) for url in candidates]
    try:
        for future in as_completed(futures, timeout=4):
            try:
                return future.result()
            except Exception:
                continue
    except TimeoutError:
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)
    # Some servers reject probes but accept normal video requests.
    return candidates[0], 0


def get_file_metadata(fid):
    """
    Fetch and unmask the real filename from DiskWala API.
    DiskWala masks random characters with '*' on each request.
    Merging responses reconstructs the full unmasked name.
    """
    chars = None
    file_info = {}
    for _ in range(1):
        try:
            req = urllib.request.Request(
                'https://udapi.diskwala.com/api/v1/file/temp_info',
                data=json.dumps({'id': fid}).encode('utf-8'),
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                    'Content-Type': 'application/json',
                    'Origin': 'https://www.diskwala.com',
                    'Referer': 'https://www.diskwala.com/'
                }
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                file_info = data.get('fileInfo', {})
                raw_name = file_info.get('name', '')
                if not chars:
                    chars = list(raw_name)
                else:
                    for i, c in enumerate(raw_name):
                        if i < len(chars) and chars[i] == '*' and c != '*':
                            chars[i] = c
        except Exception:
            pass

    unmasked_name = "".join(chars) if chars else None

    size_bytes = file_info.get('size', 0)
    size_mb = round(size_bytes / (1024 * 1024), 2) if size_bytes else 'Unknown'

    return {
        'name': unmasked_name or f"Diskwala_{fid}.mp4",
        'size': size_mb,
        'type': file_info.get('type', 'video/mp4')
    }


def resolve_link(url):
    if not url:
        return {'success': False, 'message': 'No URL provided.'}

    # Extract 24-character hex MongoDB ObjectId if present
    match = re.fullmatch(r'(?:https?://)?(?:www\.)?diskwala\.com/app/([a-fA-F0-9]{24})/?(?:[?#].*)?', url, re.I)
    if re.fullmatch(r'[a-fA-F0-9]{24}', url):
        match = re.fullmatch(r'([a-fA-F0-9]{24})', url)
    file_id = match.group(1).lower() if match else None

    # Step 1: If it's a Diskwala URL or has a 24-hex file ID
    if file_id:
        fid = file_id or url.split('/')[-1]
        
        # Step A: Fetch real file metadata with auto-unmasking
        file_meta = {'name': f'Diskwala_{fid}.mp4', 'size': 'Unknown', 'type': 'video/mp4'}

        # Step B: Fast check on extraction resolvers
        resolver_attempts = [
            f"https://thediskwala.com/api/diskwala-free?url=https%3A%2F%2Fwww.diskwala.com%2Fapp%2F{fid}",
            f"https://playdiskwala.in/api/diskwala-free?url=https%3A%2F%2Fwww.diskwala.com%2Fapp%2F{fid}"
        ]

        failures = []
        for res_url in resolver_attempts:
            try:
                ref = 'https://thediskwala.com/' if 'thediskwala' in res_url else 'https://playdiskwala.in/'
                req = urllib.request.Request(
                    res_url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json',
                        'Referer': ref
                    }
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if data.get('success'):
                        direct_url = data.get('directUrl')
                        if not direct_url and data.get('url'):
                            parsed = urllib.parse.urlparse(data['url'])
                            qs = urllib.parse.parse_qs(parsed.query)
                            if 'url' in qs:
                                direct_url = qs['url'][0]
                            elif data['url'].startswith('http'):
                                direct_url = data['url']
                            else:
                                base_domain = 'https://thediskwala.com' if 'thediskwala' in res_url else 'https://playdiskwala.in'
                                direct_url = base_domain + data['url']

                        if direct_url:
                            relay = urllib.parse.urljoin(res_url, data.get('url') or '')
                            base = urllib.parse.urlsplit(res_url)
                            parsed_relay = urllib.parse.urlsplit(relay)
                            candidates = [direct_url]
                            if parsed_relay.netloc == base.netloc and parsed_relay.path == '/api/stream':
                                candidates.append(relay)
                            upstream, total_bytes = choose_stream(candidates)
                            fallback = next((item for item in candidates if item != upstream), '')
                            stream_path = '/api/stream?' + urllib.parse.urlencode({'url': upstream, 'fallback': fallback})
                            if total_bytes:
                                file_meta['size'] = round(total_bytes / (1024 * 1024), 2)
                            return {
                                'success': True,
                                'stream_url': stream_path,
                                'direct_url': direct_url,
                                'download_url': stream_path + '&download=1&name=' + urllib.parse.quote(data.get('title') or file_meta['name'], safe=''),
                                'is_preview': bool(data.get('isFreeTeaser')), 
                                'filename': data.get('title') or file_meta.get('name') or f"Diskwala_{fid}.mp4",
                                'thumb': data.get('thumb'),
                                'size_mb': file_meta.get('size') or 'Ready',
                                'file_type': file_meta.get('type') or 'video/mp4'
                            }
                    failures.append('Resolver returned no usable stream URL.')
            except Exception as exc:
                failures.append(type(exc).__name__ + ': ' + str(exc))

        # Step C: If automated web extraction is protected by DiskWala for this link
        return {
            'success': False,
            'locked': True,
            'file_id': fid,
            'filename': file_meta.get('name', f"Diskwala_{fid}.mp4"),
            'size_mb': file_meta.get('size', 'Unknown'),
            'file_type': file_meta.get('type', 'video/mp4'),
            'app_url': f"https://www.diskwala.com/app/{fid}",
            'message': 'No stream could be resolved. The resolver may be unavailable or the file may require access through Diskwala. Try again or open the original link.',
            'details': failures
        }

    # If it's already a direct video URL (.mp4, .m3u8, .webm, .mov, .mkv)
    if any(ext in url.lower() for ext in ['.mp4', '.m3u8', '.webm', '.mov', '.mkv']):
        return {
            'success': True,
            'stream_url': url,
            'filename': url.split('/')[-1].split('?')[0],
            'size_mb': 'Direct Stream',
            'file_type': 'video'
        }

    return {
        'success': False,
        'message': 'Unrecognized link format. Paste a direct video URL or Diskwala link.'
    }


class VideoServerHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Range')
        self.end_headers()

    def do_GET(self):
        # Ignore favicon requests cleanly
        if self.path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Serve player.html directly at root /
        if self.path in ('/', ''):
            self.path = '/player.html'

        # Proxy video stream requests
        if self.path.startswith('/api/stream'):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            target_url = qs.get('url', [''])[0]

            if not target_url:
                self.send_error(400, "Missing url parameter")
                return

            self.proxy_stream(target_url, qs.get('name', ['video.mp4'])[0] if qs.get('download') == ['1'] else None, qs.get('fallback', [''])[0])
            return

        super().do_GET()

    def do_POST(self):
        if self.path == '/api/resolve':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                target_url = data.get('url', '').strip()
                result = resolve_link(target_url)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'success': False, 'error': str(e)}).encode('utf-8'))
        else:
            self.send_error(404, "Endpoint not found")

    def proxy_stream(self, target_url, download_name=None, fallback_url=None):
        if urllib.parse.urlsplit(target_url).scheme not in ('http', 'https'):
            self.send_error(400, 'Only HTTP(S) streams are supported')
            return
        headers_sent = False
        upstream_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept-Encoding': 'identity'
        }
        
        # Forward Range header for video seeking
        client_range = self.headers.get('Range')
        if client_range:
            upstream_headers['Range'] = client_range

        try:
            # Retry the alternate route only before sending headers/body to the browser.
            resp = None
            for candidate in dict.fromkeys(filter(None, (target_url, fallback_url))):
                if urllib.parse.urlsplit(candidate).scheme not in ('http', 'https'):
                    continue
                try:
                    req = urllib.request.Request(candidate, headers=upstream_headers)
                    resp = urllib.request.urlopen(req, timeout=10)
                    if any(t in resp.headers.get('Content-Type', '').lower() for t in ('text/html', 'application/json')):
                        resp.close()
                        resp = None
                        raise ValueError('Source returned a webpage instead of video')
                    break
                except Exception:
                    if candidate == fallback_url or not fallback_url:
                        raise
            if resp is None:
                raise ValueError('No usable stream route')
            with resp:
                self.send_response(resp.status)
                if download_name:
                    self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + urllib.parse.quote(download_name, safe=''))
                
                # Forward relevant media headers
                for header_name in ['Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges']:
                    header_val = resp.headers.get(header_name)
                    if header_val:
                        self.send_header(header_name, header_val)

                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()

                headers_sent = True
                # Stream chunks safely
                while True:
                    # read1 forwards available bytes immediately instead of waiting
                    # for a full 64 KiB on a slow upstream.
                    chunk = resp.read1(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as exc:
            if not headers_sent:
                self.send_response(exc.code)
                if exc.headers.get('Content-Range'):
                    self.send_header('Content-Range', exc.headers['Content-Range'])
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            if not headers_sent:
                self.send_error(502, f"Upstream stream failed: {exc}")


def run_server():
    socketserver.TCPServer.allow_reuse_address = True
    print("=" * 60)
    print(f" Clean Video Player Server running at: http://127.0.0.1:{PORT}")
    print(f" Direct Player link: http://127.0.0.1:{PORT}/player.html")
    print(f" Serving directory: {DIRECTORY}")
    print(" Press Ctrl+C in terminal to stop.")
    print("=" * 60)
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), VideoServerHandler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
