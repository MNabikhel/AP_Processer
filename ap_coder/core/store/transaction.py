"""Several of the Store's calls kept in one transaction by a caller outside the store (D-189): the JD Edwards sync's
page (its rows, where the read is and the sync's lease: all of it kept, or none, B-1918), its POs closed a few
hundred at a time."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class TransactionMixin:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Every call on this database inside, in this thread, joins one transaction on one connection (a ``session``
        is opened for it unless one is open): committed at the end, all of it undone when an exception leaves it.
        Inside another transaction it is part of that one. The few writes that must commit on their own (a queue's
        claim, a try at logging in: ``StoreCore._begin_alone``) refuse inside it (``NestedWrite``, B-1995)."""
        with self.session(), self._conn():
            yield
