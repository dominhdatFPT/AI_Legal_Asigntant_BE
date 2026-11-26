"""Bounded, process-local cache for public catalogue data only.

Callers must never use this for user data, authorization decisions or fallbacks.
"""
from collections import OrderedDict
from copy import deepcopy
from pickle import dumps
from threading import Condition, RLock
from time import monotonic

NAMESPACES = frozenset({"procedures", "procedure_domains", "classifications", "dynamic_templates", "custom_templates", "templates"})
MAX_ENTRIES = 256
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRY_BYTES = 512 * 1024


class CatalogCache:
    def __init__(self):
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._entries = OrderedDict()
        self._generations = {name: 0 for name in NAMESPACES}
        self._inflight = set()
        self._bytes = 0

    def invalidate(self, namespace):
        if namespace not in NAMESPACES:
            raise ValueError("Unknown cache namespace")
        with self._condition:
            self._generations[namespace] += 1
            for key in list(self._entries):
                if key[0] == namespace:
                    self._bytes -= self._entries.pop(key)[2]
            self._condition.notify_all()

    def clear(self):
        for namespace in NAMESPACES:
            self.invalidate(namespace)

    def get_or_load(self, namespace, key, ttl, loader):
        if namespace not in NAMESPACES:
            raise ValueError("Unknown cache namespace")
        cache_key = (namespace, key)
        with self._condition:
            while True:
                entry = self._entries.get(cache_key)
                if entry is not None:
                    if entry[0] > monotonic():
                        self._entries.move_to_end(cache_key)
                        return deepcopy(entry[1])
                    self._bytes -= self._entries.pop(cache_key)[2]
                generation = self._generations[namespace]
                flight = (cache_key, generation)
                if flight not in self._inflight:
                    self._inflight.add(flight)
                    break
                self._condition.wait()
        try:
            value = loader()
            # None denotes a failed or unavailable lookup and is never cached.
            size = len(dumps(value, protocol=4)) if value is not None else 0
            if value is not None and ttl > 0 and size <= MAX_ENTRY_BYTES:
                stored = deepcopy(value)
                with self._condition:
                    if self._generations[namespace] == generation:
                        previous = self._entries.pop(cache_key, None)
                        if previous:
                            self._bytes -= previous[2]
                        self._entries[cache_key] = (monotonic() + ttl, stored, size)
                        self._bytes += size
                        while len(self._entries) > MAX_ENTRIES or self._bytes > MAX_BYTES:
                            _, old = self._entries.popitem(last=False)
                            self._bytes -= old[2]
            return value
        finally:
            with self._condition:
                self._inflight.discard(flight)
                self._condition.notify_all()


catalog_cache = CatalogCache()
