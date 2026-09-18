"""One LexiTrack at a time.

Two copies of the app would mean two Telegram pollers on one token — Telegram
rejects one with "Conflict: terminated by other getUpdates request" and
updates go missing — and two processes writing one database, which is the
thing the whole design avoids. So the first instance listens on a local
socket, and a second launch connects to it, asks it to show its window, and
exits.

A local socket rather than a lock file: a lock file survives a crash and has
to be cleaned up by guesswork; a socket disappears with the process that
owned it.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core import paths

_TIMEOUT_MS = 800


def server_name() -> str:
    """One name per data folder, so a test instance never meets the real one."""
    digest = hashlib.sha1(str(paths.data_dir()).encode()).hexdigest()[:12]
    return f"LexiTrack-{digest}"


def notify_running_instance(name: str | None = None) -> bool:
    """Ask a running LexiTrack to show itself. ``True`` if one answered."""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(_TIMEOUT_MS):
        return False
    socket.write(b"show")
    socket.flush()
    socket.waitForBytesWritten(_TIMEOUT_MS)
    socket.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Listens for later launches and asks the window to come forward."""

    show_requested = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        if self._server.listen(self._name):
            return True
        # A server left behind by a crash: nobody answered above, so remove
        # the stale name and take it over.
        QLocalServer.removeServer(self._name)
        return self._server.listen(self._name)

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        """Read the one-word request and hang up.

        A short blocking read rather than waiting for ``readyRead``: the other
        launch writes and disconnects at once, and a disconnected socket can be
        cleaned up before its data signal is ever delivered.
        """
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            if not socket.bytesAvailable():
                socket.waitForReadyRead(_TIMEOUT_MS)
            request = bytes(socket.readAll()).strip()
            socket.disconnectFromServer()
            socket.deleteLater()
            if request == b"show":
                self.show_requested.emit()
