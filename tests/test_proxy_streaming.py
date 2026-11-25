"""Integration tests for the proxy embedded in the Actions workflow."""
import http.client
import os
import socket
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/llm-test.yml'


class Upstream(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    release = threading.Event()

    def do_GET(self):
        if self.path == '/missing':
            self.send_response(404)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '17')
            self.end_headers()
            self.wfile.write(b'{"error":"nope"}\n')
        elif self.path == '/empty':
            self.send_response(204)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        def write_chunk(data):
            self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
            self.wfile.flush()
        write_chunk(b'{"message":{"content":"first"}}\n')
        self.release.wait(5)
        write_chunk(b'{"message":{"content":"second"},"done":true}\n')
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ProxyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.port = free_port()
        workflow = WORKFLOW.read_text()
        block = workflow.split("          cat > /tmp/ollama_proxy.py <<'PY'\n", 1)[1].split('          PY\n', 1)[0]
        script = '\n'.join(line[10:] for line in block.splitlines())
        script = script.replace('127.0.0.1:11434', f'127.0.0.1:{cls.upstream.server_port}')
        script = script.replace('("127.0.0.1", 8000)', f'("127.0.0.1", {cls.port})')
        cls.tmp = tempfile.TemporaryDirectory()
        script = script.replace('/tmp/ollama-last-request', os.path.join(cls.tmp.name, 'activity'))
        cls.process = subprocess.Popen(['python3', '-u', '-c', script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # Wait for the listener without relying on a fixed startup delay.
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=.1):
                    break
            except OSError:
                threading.Event().wait(.02)
        else:
            raise RuntimeError('Proxy failed to start')

    @classmethod
    def tearDownClass(cls):
        Upstream.release.set()
        cls.process.terminate()
        cls.process.communicate(timeout=5)
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.tmp.cleanup()

    def test_streaming_before_completion_and_cors(self):
        Upstream.release.clear()
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            conn.request('POST', '/api/chat', '{"stream":true}', {'Content-Type': 'application/json'})
            res = conn.getresponse()
            self.assertEqual(res.status, 200)
            self.assertEqual(res.getheader('Access-Control-Allow-Origin'), '*')
            self.assertEqual(res.getheader('Transfer-Encoding'), 'chunked')
            self.assertIsNone(res.getheader('Content-Length'))
            # This read must finish while the upstream is still waiting on release.
            self.assertIn(b'first', res.readline())
            self.assertFalse(Upstream.release.is_set())
            Upstream.release.set()
            self.assertIn(b'second', res.readline())
            self.assertEqual(res.read(), b'')
            conn.request('GET', '/tags')  # the downstream connection is reusable
            self.assertEqual(conn.getresponse().read(), b'{}')
        finally:
            Upstream.release.set()
            conn.close()

    def test_errors_options_and_empty_response(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            conn.request('OPTIONS', '/api/chat')
            res = conn.getresponse()
            self.assertEqual(res.status, 204)
            self.assertEqual(res.getheader('Access-Control-Allow-Origin'), '*')
            res.read()
            conn.request('GET', '/missing')
            res = conn.getresponse()
            self.assertEqual(res.status, 404)
            self.assertEqual(res.read(), b'{"error":"nope"}\n')
            conn.request('GET', '/empty')
            res = conn.getresponse()
            self.assertEqual(res.status, 204)
            self.assertIsNone(res.getheader('Transfer-Encoding'))
            res.read()
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
