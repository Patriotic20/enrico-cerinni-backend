import json
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_staff
from app.database import get_db
from app.models.app_setting import AppSetting
from app.models.user import User
from app.schemas.common import ResponseModel

router = APIRouter(prefix="/labels", tags=["Labels"], dependencies=[Depends(require_staff)])

TEMPLATE_KEY = "label_template"


class LabelTemplate(BaseModel):
    """Barcode sticker layout. Bounds keep a bad value from producing an unprintable page."""

    mode: Literal["roll", "a4"] = "roll"
    code_type: Literal["code128", "datamatrix", "qrcode"] = "code128"
    width_mm: float = Field(58, ge=15, le=120)
    height_mm: float = Field(40, ge=10, le=100)
    roll_cols: int = Field(1, ge=1, le=4)  # labels side by side on multi-across rolls
    a4_cols: int = Field(3, ge=1, le=8)
    a4_rows: int = Field(8, ge=1, le=20)
    a4_margin_mm: float = Field(8, ge=0, le=20)
    a4_gap_mm: float = Field(2, ge=0, le=10)
    show_header: bool = True
    show_name: bool = True
    show_price: bool = True
    show_variant: bool = True
    show_sku_text: bool = True
    header_text: str = Field("Enrico Cerinni", max_length=60)
    font_size: float = Field(9, ge=5, le=20)
    bar_height_mm: float = Field(12, ge=5, le=60)
    bar_width: float = Field(1.5, ge=1, le=4)


@router.get("/template", response_model=ResponseModel)
def get_template(db: Session = Depends(get_db)):
    row = db.get(AppSetting, TEMPLATE_KEY)
    # Merge over defaults so fields added later still get a value.
    data = LabelTemplate(**json.loads(row.value)) if row else LabelTemplate()
    return ResponseModel(success=True, data=data.model_dump())


@router.put("/template", response_model=ResponseModel)
def save_template(
    payload: LabelTemplate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    value = payload.model_dump_json()
    row = db.get(AppSetting, TEMPLATE_KEY)
    if row:
        row.value = value
        row.updated_by = current_user.id
    else:
        db.add(AppSetting(key=TEMPLATE_KEY, value=value, updated_by=current_user.id))
    db.commit()
    return ResponseModel(success=True, data=payload.model_dump(), message="Shablon saqlandi")
