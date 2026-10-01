from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reservation:
    start_s: int
    end_s: int
    owner_id: str


class ResourceCalendar:
    def __init__(self):
        self.reservations: list[Reservation] = []

    def clone(self) -> "ResourceCalendar":
        result = ResourceCalendar()
        result.reservations = self.reservations.copy()
        return result

    def is_free(self, start_s: int, end_s: int) -> bool:
        if end_s <= start_s:
            return False

        for reservation in self.reservations:
            overlap = (
                start_s < reservation.end_s
                and reservation.start_s < end_s
            )
            if overlap:
                return False

        return True

    def reserve(self, start_s: int, end_s: int, owner_id: str) -> None:
        if end_s <= start_s:
            raise ValueError("Reservation must have positive duration.")

        if not self.is_free(start_s, end_s):
            raise ValueError(
                f"Resource is already reserved for interval [{start_s}, {end_s})."
            )

        self.reservations.append(
            Reservation(start_s=start_s, end_s=end_s, owner_id=owner_id)
        )

        self.reservations.sort(
            key=lambda item: (item.start_s, item.end_s, item.owner_id)
        )

    def next_free_time(self, start_s: int, duration_s: int) -> int:
        if duration_s <= 0:
            raise ValueError("Duration must be positive.")

        candidate = start_s

        while True:
            end_s = candidate + duration_s
            blocking = None

            for reservation in self.reservations:
                overlap = (
                    candidate < reservation.end_s
                    and reservation.start_s < end_s
                )
                if overlap:
                    blocking = reservation
                    break

            if blocking is None:
                return candidate

            candidate = max(candidate, blocking.end_s)

    def get_reservations(self) -> list[Reservation]:
        return self.reservations.copy()

    def clear(self) -> None:
        self.reservations.clear()

    def __repr__(self) -> str:
        return f"ResourceCalendar(reservations={self.reservations})"
