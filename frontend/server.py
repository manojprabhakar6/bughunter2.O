from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        request_path = urlsplit(self.path).path
        requested_file = ROOT / request_path.lstrip("/")
        if not requested_file.is_file():
            self.path = "/index.html"
        super().do_GET()


if __name__ == "__main__":
    address = ("127.0.0.1", 5173)
    print(f"Bug Hunter frontend available at http://bug-hunter.localhost:{address[1]}")
    ThreadingHTTPServer(address, FrontendHandler).serve_forever()
