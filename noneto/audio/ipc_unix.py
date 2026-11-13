"""Private Unix socketpair inherited by mpv; closing the owner also stops mpv."""
import socket


class UnixIPC:
    def __init__(self):
        self.pipe, self.child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self.pipe.settimeout(0.1)
        self.argument = f"--input-ipc-client=fd://{self.child.fileno()}"
        self.spawn_options = {"pass_fds": (self.child.fileno(),)}

    def connect(self, process):
        self.child.close()

    def read(self, size):
        try:
            return self.pipe.recv(size)
        except socket.timeout:
            return None

    def write(self, data, timeout=5):
        try:
            return self.pipe.send(data)
        except (socket.timeout, BlockingIOError):
            return 0

    def close(self):
        self.pipe.close()
        self.child.close()
