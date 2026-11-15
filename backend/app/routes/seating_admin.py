"""Phase 3A admin seating API (administrators only; role is read from the database by require_admin)."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from ..database import get_db
from ..deps import require_admin
from .. import seating as S
from ..seating_schemas import (LayoutIn, LayoutUpdate, CategoryIn, CategoryUpdate, StandIn, StandUpdate, SectionIn, SectionUpdate,
                               RowIn, RowUpdate, SeatIn, SeatUpdate, AssignLayoutIn, SeatStatusIn)

router = APIRouter(prefix="/admin", tags=["admin-seating"], dependencies=[Depends(require_admin)])


# ---- layouts
@router.get("/seating/layouts")
def layouts(stadium_id: Optional[int] = None, db=Depends(get_db)):
    return S.list_layouts(db, stadium_id)


@router.post("/seating/layouts", status_code=201)
def create_layout(body: LayoutIn, admin: dict = Depends(require_admin), db=Depends(get_db)):
    return S.create_layout(db, body.stadium_id, body.name, body.model_dump(include={"categories", "stands"}), admin["_id"])


@router.get("/seating/layouts/{layout_id}")
def layout(layout_id: int, db=Depends(get_db)):
    return S.layout_tree(db, layout_id)


@router.put("/seating/layouts/{layout_id}")
def edit_layout(layout_id: int, body: LayoutUpdate, db=Depends(get_db)):
    return S.update_layout(db, layout_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/layouts/{layout_id}", status_code=204)
def remove_layout(layout_id: int, db=Depends(get_db)):
    S.delete_layout(db, layout_id)


# ---- categories and prices
@router.post("/seating/layouts/{layout_id}/categories", status_code=201)
def add_category(layout_id: int, body: CategoryIn, db=Depends(get_db)):
    return S.create_category(db, layout_id, body.model_dump())


@router.put("/seating/categories/{category_id}")
def edit_category(category_id: int, body: CategoryUpdate, db=Depends(get_db)):
    return S.update_category(db, category_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/categories/{category_id}", status_code=204)
def remove_category(category_id: int, db=Depends(get_db)):
    S.delete_category(db, category_id)


# ---- stands, sections, rows, seats
@router.post("/seating/layouts/{layout_id}/stands", status_code=201)
def add_stand(layout_id: int, body: StandIn, db=Depends(get_db)):
    return S.add_stand(db, layout_id, body.model_dump())


@router.put("/seating/stands/{stand_id}")
def edit_stand(stand_id: int, body: StandUpdate, db=Depends(get_db)):
    return S.update_stand(db, stand_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/stands/{stand_id}", status_code=204)
def remove_stand(stand_id: int, db=Depends(get_db)):
    S.delete_stand(db, stand_id)


@router.post("/seating/stands/{stand_id}/sections", status_code=201)
def add_section(stand_id: int, body: SectionIn, db=Depends(get_db)):
    return S.add_section(db, stand_id, body.model_dump())


@router.put("/seating/sections/{section_id}")
def edit_section(section_id: int, body: SectionUpdate, db=Depends(get_db)):
    return S.update_section(db, section_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/sections/{section_id}", status_code=204)
def remove_section(section_id: int, db=Depends(get_db)):
    S.delete_section(db, section_id)


@router.post("/seating/sections/{section_id}/rows", status_code=201)
def add_row(section_id: int, body: RowIn, db=Depends(get_db)):
    return S.add_row(db, section_id, body.model_dump())


@router.put("/seating/rows/{row_id}")
def edit_row(row_id: int, body: RowUpdate, db=Depends(get_db)):
    return S.update_row(db, row_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/rows/{row_id}", status_code=204)
def remove_row(row_id: int, db=Depends(get_db)):
    S.delete_row(db, row_id)


@router.get("/seating/rows/{row_id}/seats")
def row_seats(row_id: int, db=Depends(get_db)):
    return S.list_row_seats(db, row_id)


@router.post("/seating/rows/{row_id}/seats", status_code=201)
def add_seat(row_id: int, body: SeatIn, db=Depends(get_db)):
    return S.add_seat(db, row_id, body.model_dump())


@router.put("/seating/seats/{seat_id}")
def edit_seat(seat_id: int, body: SeatUpdate, db=Depends(get_db)):
    return S.update_seat(db, seat_id, body.model_dump(exclude_unset=True))


@router.delete("/seating/seats/{seat_id}", status_code=204)
def remove_seat(seat_id: int, db=Depends(get_db)):
    S.delete_seat(db, seat_id)


# ---- match assignment and match-specific inventory
@router.put("/matches/{match_id}/seating")
def assign(match_id: int, body: AssignLayoutIn, db=Depends(get_db)):
    return S.assign_layout(db, match_id, body.layout_id)


@router.delete("/matches/{match_id}/seating", status_code=204)
def unassign(match_id: int, db=Depends(get_db)):
    S.unassign_layout(db, match_id)


@router.get("/matches/{match_id}/seat-inventory/summary")
def inventory_summary(match_id: int, db=Depends(get_db)):
    return S.inventory_summary(db, match_id)


@router.get("/matches/{match_id}/seat-inventory")
def inventory(match_id: int, section_id: Optional[int] = None, row_id: Optional[int] = None, status: Optional[str] = None,
              limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db=Depends(get_db)):
    return S.list_inventory(db, match_id, section_id, row_id, status, limit, offset)


@router.put("/matches/{match_id}/seat-inventory/status")
def inventory_status(match_id: int, body: SeatStatusIn, db=Depends(get_db)):
    return S.set_seat_status(db, match_id, body.seat_ids, body.status)
