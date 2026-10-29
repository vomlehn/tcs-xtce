"""Managed byte buffers.

A :class:`ManagedBuffer` allocates its storage up front and keeps it between
uses: clearing only resets the logical length, and writes go into the
existing allocation with slice assignment.  Buffers are owned by the objects
that use them (normally a :class:`xtce.Document`), so the memory lives as
long as that object and is released with it.
"""

from __future__ import annotations

DEFAULT_CAPACITY = 64 * 1024


class ManagedBuffer:
    """A preallocated, reusable byte buffer."""

    __slots__ = ("_data", "_len")

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._data = bytearray(capacity)
        self._len = 0

    def __len__(self) -> int:
        return self._len

    @property
    def capacity(self) -> int:
        """Bytes currently allocated (always at least ``len(self)``)."""
        return len(self._data)

    def clear(self) -> None:
        """Discard the contents but keep the allocation."""
        self._len = 0

    def reserve(self, additional: int) -> None:
        """Ensures room for at least ``additional`` more bytes.

        Grows the allocation to the larger of what is needed and twice its
        current size.

        Args:
            additional: How many bytes past the current length must fit.

        Raises:
            BufferError: The buffer must grow while a view of it is held.
        """
        need = self._len + additional
        if need > len(self._data):
            grow = max(need, 2 * len(self._data)) - len(self._data)
            try:
                self._data.extend(bytes(grow))
            except BufferError as e:
                raise BufferError("cannot grow a ManagedBuffer while a view of "
                                  "it is held; release views returned by "
                                  "view() first") from e

    def resize(self, n: int) -> None:
        """Sets the length to ``n`` bytes, zero filling any new bytes.

        Args:
            n: The new length in bytes.
        """
        if n > self._len:
            self.reserve(n - self._len)
            self._data[self._len : n] = bytes(n - self._len)
        self._len = n

    def write(self, data) -> None:
        """Appends bytes-like ``data``.

        Args:
            data: The bytes to append.
        """
        n = len(data)
        self.reserve(n)
        end = self._len + n
        self._data[self._len : end] = data
        self._len = end

    def load(self, data) -> None:
        """Replace the contents with ``data``, reusing the allocation."""
        self._len = 0
        self.write(data)

    def load_from(self, stream, chunk: int = 64 * 1024) -> int:
        """Replaces the contents with everything read from a binary stream.

        Args:
            stream: A binary stream; its ``readinto`` is used if it has one,
                    else its ``read``.
            chunk:  How many bytes to grow the buffer by when it is full.

        Returns:
            The number of bytes read, the buffer's new length.
        """
        self._len = 0
        while True:
            # Fill the existing allocation first; grow only when it is full.
            if self._len == len(self._data):
                self.reserve(chunk)
            view = memoryview(self._data)[self._len :]
            try:
                if hasattr(stream, "readinto"):
                    n = stream.readinto(view)
                else:
                    # Ask for no more than fits, so the block can be copied in.
                    block = stream.read(len(view))
                    n = len(block)
                    view[:n] = block
            finally:
                view.release()
            if not n:
                return self._len
            self._len += n

    def view(self) -> memoryview:
        """A zero-copy view of the contents, valid until the buffer changes.

        Release it (``view.release()`` or a ``with`` block) before the buffer
        needs to grow.
        """
        return memoryview(self._data)[: self._len]

    def getvalue(self) -> bytes:
        """A copy of the contents."""
        return bytes(self._data[: self._len])
