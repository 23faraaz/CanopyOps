"""One-connection plaintext PostgreSQL fault proxy for an isolated local test only.
Forward all traffic except the server's successful COMMIT response, then disconnect.
This is a real socket fault, not a TLS/RDS test or a production proxy.
"""

import socket
import struct
import threading


class DropCommitResponse:
    def __init__(self, upstream="postgres", port=5432):
        self.upstream = (upstream, port)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(1)
        self.listener.settimeout(15)
        self.dropped = threading.Event()
        self.error = None
        self.sockets = []
        self.thread = threading.Thread(target=self._accept, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _close(self):
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.listener.close()

    def _accept(self):
        try:
            client, _ = self.listener.accept()
            upstream = socket.create_connection(self.upstream, timeout=10)
            upstream.settimeout(None)
            self.sockets = [client, upstream]

            def forward():
                try:
                    while True:
                        data = client.recv(65536)
                        if not data:
                            break
                        upstream.sendall(data)
                except OSError:
                    pass

            threading.Thread(target=forward, daemon=True).start()
            pending = b""
            while True:
                data = upstream.recv(65536)
                if not data:
                    break
                pending += data
                while len(pending) >= 5:
                    length = struct.unpack("!I", pending[1:5])[0]
                    if length < 4 or length > 16 * 1024 * 1024:
                        raise ValueError("Unexpected PostgreSQL message length")
                    if len(pending) < length + 1:
                        break
                    message, pending = pending[: length + 1], pending[length + 1 :]
                    if message[:1] == b"C" and message[5:] == b"COMMIT\0":
                        self.dropped.set()
                        return  # Suppress success and ReadyForQuery; force a client disconnect.
                    client.sendall(message)
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            self._close()

    def __exit__(self, *_):
        self._close()
        self.thread.join(timeout=2)
