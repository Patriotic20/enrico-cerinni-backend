import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models import Brand
from app.schemas.brand import BrandCreate, BrandUpdate, BrandResponse
from app.api.deps import get_current_user, require_staff
from app.models.user import User
from app.schemas.common import ResponseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/brands", tags=["brands"])


@router.post("", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def create_brand(
    brand: BrandCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new brand"""
    try:
        db_brand = Brand(**brand.dict())
        db.add(db_brand)
        db.commit()
        db.refresh(db_brand)
        return ResponseModel(
            success=True,
            message="Brand created successfully",
            data=BrandResponse(
                id=db_brand.id,
                name=db_brand.name,
                description=db_brand.description,
                logo_url=db_brand.logo_url,
                created_at=db_brand.created_at.isoformat(),
                updated_at=db_brand.updated_at.isoformat() if db_brand.updated_at else None,
            ),
        )
    except Exception as e:
        logger.exception("Failed to create brand")
        return ResponseModel(success=False, message="Failed to create brand")


@router.get("", response_model=ResponseModel)
def get_brands(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all brands"""
    try:
        brands = db.query(Brand).offset(skip).limit(limit).all()
        return ResponseModel(
            success=True,
            message="Brands fetched successfully",
            data=[
                BrandResponse(
                    id=brand.id,
                    name=brand.name,
                    description=brand.description,
                    logo_url=brand.logo_url,
                    created_at=brand.created_at.isoformat(),
                    updated_at=brand.updated_at.isoformat() if brand.updated_at else None,
                )
                for brand in brands
            ],
        )
    except Exception as e:
        logger.exception("Failed to fetch brands")
        return ResponseModel(success=False, message="Failed to fetch brands")


@router.get("/{brand_id}", response_model=ResponseModel)
def get_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific brand by ID"""
    try:
        brand = db.query(Brand).filter(Brand.id == brand_id).first()
        if not brand:
            return ResponseModel(success=False, message="Brand not found")
        
        return ResponseModel(
            success=True,
            message="Brand fetched successfully",
            data=BrandResponse(
                id=brand.id,
                name=brand.name,
                description=brand.description,
                logo_url=brand.logo_url,
                created_at=brand.created_at.isoformat(),
                updated_at=brand.updated_at.isoformat() if brand.updated_at else None,
            ),
        )
    except Exception as e:
        logger.exception("Failed to fetch brand")
        return ResponseModel(success=False, message="Failed to fetch brand")


@router.put("/{brand_id}", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def update_brand(
    brand_id: int,
    brand: BrandUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a brand"""
    try:
        db_brand = db.query(Brand).filter(Brand.id == brand_id).first()
        if not db_brand:
            return ResponseModel(success=False, message="Brand not found")

        for field, value in brand.dict(exclude_unset=True).items():
            setattr(db_brand, field, value)

        db.commit()
        db.refresh(db_brand)
        return ResponseModel(
            success=True,
            message="Brand updated successfully",
            data=BrandResponse(
                id=db_brand.id,
                name=db_brand.name,
                description=db_brand.description,
                logo_url=db_brand.logo_url,
                created_at=db_brand.created_at.isoformat(),
                updated_at=db_brand.updated_at.isoformat() if db_brand.updated_at else None,
            ),
        )
    except Exception as e:
        logger.exception("Failed to update brand")
        return ResponseModel(success=False, message="Failed to update brand")


@router.delete("/{brand_id}", response_model=ResponseModel, dependencies=[Depends(require_staff)])
def delete_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a brand"""
    try:
        db_brand = db.query(Brand).filter(Brand.id == brand_id).first()
        if not db_brand:
            return ResponseModel(success=False, message="Brand not found")

        db.delete(db_brand)
        db.commit()
        return ResponseModel(success=True, message="Brand deleted successfully")
    except Exception as e:
        logger.exception("Failed to delete brand")
        return ResponseModel(success=False, message="Failed to delete brand")
