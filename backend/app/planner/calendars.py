from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reservation:
    start_s: int
    end_s: int
    owner_id: str


class ResourceCalendar:
    """Half-open interval calendar [start_s, end_s)."""

    def __init__(self):
        self.reservations: list[Reservation] = []

    def clone(self) -> "ResourceCalendar":
        result = ResourceCalendar()
        result.reservations = self.reservations.copy()
        return result

    def is_free(
        self,
        start_s: int,
        end_s: int,
        *,
        ignore_owner: str | None = None,
    ) -> bool:
        if end_s <= start_s:
            return False
        for reservation in self.reservations:
            if ignore_owner is not None and reservation.owner_id == ignore_owner:
                continue
            if start_s < reservation.end_s and reservation.start_s < end_s:
                return False
        return True

    def reserve(
        self,
        start_s: int,
        end_s: int,
        owner_id: str,
        *,
        allow_same_owner: bool = False,
    ) -> None:
        if end_s <= start_s:
            raise ValueError("Reservation must have positive duration.")
        if not self.is_free(
            start_s,
            end_s,
            ignore_owner=owner_id if allow_same_owner else None,
        ):
            raise ValueError(
                f"Resource is already reserved for interval [{start_s}, {end_s})."
            )
        self.reservations.append(
            Reservation(start_s=start_s, end_s=end_s, owner_id=owner_id)
        )
        self.reservations.sort(
            key=lambda item: (item.start_s, item.end_s, item.owner_id)
        )

    def next_free_time(
        self,
        start_s: int,
        duration_s: int,
        *,
        ignore_owner: str | None = None,
    ) -> int:
        if duration_s <= 0:
            raise ValueError("Duration must be positive.")

        candidate = start_s
        while True:
            end_s = candidate + duration_s
            blocking = None
            for reservation in self.reservations:
                if ignore_owner is not None and reservation.owner_id == ignore_owner:
                    continue
                if candidate < reservation.end_s and reservation.start_s < end_s:
                    blocking = reservation
                    break
            if blocking is None:
                return candidate
            candidate = max(candidate, blocking.end_s)

    def remove_owner(self, owner_id: str) -> None:
        self.reservations = [
            r for r in self.reservations
            if r.owner_id != owner_id
        ]

    def get_reservations(self) -> list[Reservation]:
        return self.reservations.copy()

    def clear(self) -> None:
        self.reservations.clear()

    def __repr__(self) -> str:
        return f"ResourceCalendar(reservations={self.reservations})"
