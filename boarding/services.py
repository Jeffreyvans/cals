"""Automatic boarding room and bed allocation."""

from django.db import transaction

from .models import Boarder, Room


def _room_spaces():
    """Rooms with a free place, in hostel/room order, with the next free bed."""
    rooms = []
    for room in Room.objects.select_related("hostel").order_by(
        "hostel__name", "name", "pk"
    ):
        occupied = room.boarders.count()
        free = max(room.capacity - occupied, 0)
        if free:
            rooms.append({"room": room, "free": free, "next_bed": occupied + 1})
    return rooms


def allocation_plan():
    """Work out where every unallocated, active boarder should go.

    Nothing is written: the return value is a preview that the screen shows
    before a person confirms it.
    """
    boarders = list(
        Boarder.objects.filter(room__isnull=True, status=Boarder.Status.ACTIVE)
        .select_related("student__user")
        .order_by("admitted_on", "pk")
    )
    rooms = _room_spaces()
    assignments = []
    unplaced = []
    room_index = 0
    for boarder in boarders:
        placed = False
        while room_index < len(rooms):
            space = rooms[room_index]
            if space["free"] <= 0:
                room_index += 1
                continue
            assignments.append(
                {
                    "boarder": boarder,
                    "room": space["room"],
                    "bed": str(space["next_bed"]),
                }
            )
            space["free"] -= 1
            space["next_bed"] += 1
            placed = True
            break
        if not placed:
            unplaced.append(boarder)
    return {"assignments": assignments, "unplaced": unplaced}


@transaction.atomic
def allocate_rooms():
    """Apply :func:`allocation_plan`; returns the same preview with results."""
    plan = allocation_plan()
    for row in plan["assignments"]:
        Boarder.all_objects.filter(pk=row["boarder"].pk).update(
            room=row["room"], bed=row["bed"]
        )
        row["boarder"].room = row["room"]
        row["boarder"].bed = row["bed"]
    return plan
