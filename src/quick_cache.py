"""A byte-bounded result cache with independent containers and immutable arrays."""
from collections import OrderedDict
from collections.abc import MutableMapping
import copy
import sys

import numpy as np


def _arrays(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, np.ndarray):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _arrays(key, seen)
            yield from _arrays(child, seen)
    elif isinstance(value, (tuple, list, set, frozenset)):
        for child in value:
            yield from _arrays(child, seen)


def copy_result(value, *, readonly=False, share_arrays=False):
    """Copy plain result data, preserving cycles and repeated array references.

    Read-only numeric arrays own immutable bytes, so callers cannot enable writes
    on a returned cache array. Apply gets writable copies for native renderers.
    """
    memo = {}
    for array in _arrays(value):
        if share_arrays and not array.dtype.hasobject:
            cloned = array
        elif readonly and not array.dtype.hasobject:
            cloned = np.frombuffer(array.tobytes(order="C"), dtype=array.dtype).reshape(array.shape)
        else:
            cloned = copy.deepcopy(array)
            if readonly:
                cloned.flags.writeable = False
        memo[id(array)] = cloned
    return copy.deepcopy(value, memo)


def copy_preview(result, candidate):
    """Writable arrays for this preview; immutable arrays for other candidates.

    Candidate changes do not need to duplicate every cavity mesh. The controller
    passes a frozen result, and native apply implementations use one candidate.
    """
    memo = {id(array): array for array in _arrays(result)}
    selected = {key: value for key, value in result.items() if key != "candidates"}
    candidates = result.get("candidates", ())
    if 0 <= candidate < len(candidates):
        selected["candidate"] = candidates[candidate]
    for array in _arrays(selected):
        memo[id(array)] = array.copy()
    return copy.deepcopy(result, memo)


def retained_bytes(value, seen=None):
    """Conservative retained size, counting each shared backing allocation once."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, np.ndarray):
        if value.base is not None:
            size += retained_bytes(value.base, seen)
        if value.dtype.hasobject:
            size += sum(retained_bytes(item, seen) for item in value.flat)
    elif isinstance(value, memoryview):
        size += retained_bytes(value.obj, seen)
    elif isinstance(value, dict):
        size += sum(retained_bytes(key, seen) + retained_bytes(child, seen)
                    for key, child in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(retained_bytes(child, seen) for child in value)
    return size


class ResultCache(MutableMapping):
    """LRU mapping bounded by both bytes (64 MiB) and entries (8) by default."""

    def __init__(self, max_bytes=64 * 1024 * 1024, max_entries=8):
        self.max_bytes = max(0, int(max_bytes))
        self.max_entries = max(0, int(max_entries))
        self.bytes_used = 0
        self._data = OrderedDict()

    def __getitem__(self, key):
        value, _ = self._data[key]
        self._data.move_to_end(key)
        return copy_result(value, readonly=True, share_arrays=True)

    def __contains__(self, key):
        return key in self._data

    def __setitem__(self, key, value):
        if key in self._data:
            del self[key]
        # Avoid allocating a second large result merely to reject it.
        if not self.max_entries or retained_bytes((key, value)) > self.max_bytes:
            return
        copied = copy_result(value, readonly=True)
        size = retained_bytes((key, copied))
        if size > self.max_bytes:
            return
        while self._data and (len(self._data) >= self.max_entries or self.bytes_used + size > self.max_bytes):
            self.popitem(last=False)
        self._data[key] = (copied, size)
        self.bytes_used += size

    def __delitem__(self, key):
        _, size = self._data.pop(key)
        self.bytes_used -= size

    def __iter__(self):
        # Mapping.items()/values() call __getitem__, which updates LRU order.
        return iter(tuple(self._data))

    def __len__(self):
        return len(self._data)

    def move_to_end(self, key, last=True):
        self._data.move_to_end(key, last=last)

    def popitem(self, last=True):
        key, (value, size) = self._data.popitem(last=last)
        self.bytes_used -= size
        return key, copy_result(value, readonly=True, share_arrays=True)

    def clear(self):
        self._data.clear()
        self.bytes_used = 0
