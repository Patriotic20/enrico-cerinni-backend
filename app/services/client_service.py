from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from typing import List, Optional, Tuple
from decimal import Decimal
from app.models.client import Client
from app.models.sale import Sale
from app.schemas.client import ClientCreate, ClientUpdate, ClientFilter
from app.utils.helpers import paginate_query, calculate_pagination_info
from fastapi import HTTPException, status


class ClientService:
    def __init__(self, db: Session):
        self.db = db

    def create_client(self, client_data: ClientCreate) -> Client:
        """Create a new client."""
        # Check if client with same email already exists
        if client_data.phone:
            existing_client = (
                self.db.query(Client).filter(Client.phone == client_data.phone).first()
            )
            if existing_client:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Client with this phone already exists",
                )

        db_client = Client(**client_data.model_dump())
        self.db.add(db_client)
        self.db.commit()
        self.db.refresh(db_client)
        return db_client

    def get_client(self, client_id: int) -> Optional[Client]:
        """Get a client by ID."""
        return self.db.query(Client).filter(Client.id == client_id).first()

    def get_clients(self, filters: ClientFilter) -> Tuple[List[Client], dict]:
        """Get clients with filtering and pagination."""
        # Outstanding debt computed from sales (source of truth), not the
        # denormalized Client.debt_amount column which can drift.
        debt_rows = self.db.query(
            Sale.client_id.label("client_id"),
            func.coalesce(
                func.sum(Sale.total_amount - Sale.paid_amount), 0
            ).label("debt"),
        ).filter(Sale.status.in_(["debt", "partially_paid"]))

        # The debts page offers a date range; it narrows which sales the debt is
        # accrued from rather than filtering clients by their signup date.
        if filters.start_date:
            debt_rows = debt_rows.filter(Sale.created_at >= filters.start_date)
        if filters.end_date:
            debt_rows = debt_rows.filter(Sale.created_at <= filters.end_date)

        debt_subq = debt_rows.group_by(Sale.client_id).subquery()
        # Unpaid sales plus anything entered by hand via "Qarz qo'shish".
        debt_expr = func.coalesce(debt_subq.c.debt, 0) + func.coalesce(
            Client.manual_debt_adjustment, 0
        )

        # Most recent sale per client, for the "last purchase" column and the
        # active-clients card. Cancelled sales do not count as a purchase.
        last_purchase_subq = (
            self.db.query(
                Sale.client_id.label("client_id"),
                func.max(Sale.created_at).label("last_purchase"),
            )
            .filter(Sale.status != "cancelled")
            .group_by(Sale.client_id)
            .subquery()
        )

        query = (
            self.db.query(
                Client,
                debt_expr.label("debt"),
                last_purchase_subq.c.last_purchase.label("last_purchase"),
            )
            .outerjoin(debt_subq, debt_subq.c.client_id == Client.id)
            .outerjoin(last_purchase_subq, last_purchase_subq.c.client_id == Client.id)
        )

        # Apply filters
        if filters.name:
            query = query.filter(
                or_(
                    Client.first_name.ilike(f"%{filters.name}%"),
                    Client.last_name.ilike(f"%{filters.name}%"),
                )
            )

        if filters.phone:
            query = query.filter(Client.phone.ilike(f"%{filters.phone}%"))

        if filters.has_debt is not None:
            if filters.has_debt:
                query = query.filter(debt_expr > 0)
            else:
                query = query.filter(debt_expr <= 0)

        if filters.min_debt is not None:
            query = query.filter(debt_expr >= filters.min_debt)
        if filters.max_debt is not None:
            query = query.filter(debt_expr <= filters.max_debt)

        # Sorting. Client.id breaks ties so that pagination is stable — without
        # a total order Postgres may return rows differently on every page.
        sort_options = {
            "debt_amount_desc": debt_expr.desc(),
            "debt_amount_asc": debt_expr.asc(),
            "client_name_asc": Client.first_name.asc(),
            "client_name_desc": Client.first_name.desc(),
            "created_at_desc": Client.created_at.desc(),
            "created_at_asc": Client.created_at.asc(),
        }
        query = query.order_by(
            sort_options.get(filters.sort_by, debt_expr.desc()), Client.id.asc()
        )

        # Free-text search across the fields the UI advertises: name and phone.
        if filters.search:
            term = f"%{filters.search.strip()}%"
            query = query.filter(
                or_(
                    Client.first_name.ilike(term),
                    Client.last_name.ilike(term),
                    Client.phone.ilike(term),
                )
            )

        total = query.count()

        query = paginate_query(query, filters.page, filters.size)

        rows = query.all()

        # Overwrite stale column with computed outstanding debt for the response,
        # and attach the last purchase date the query just resolved.
        clients = []
        for client, debt, last_purchase in rows:
            client.debt_amount = debt
            client.last_purchase_date = last_purchase
            clients.append(client)

        # Calculate pagination info
        pagination = calculate_pagination_info(total, filters.page, filters.size)

        return clients, pagination

    def update_client(
        self, client_id: int, client_data: ClientUpdate
    ) -> Optional[Client]:
        """Update a client."""
        client = self.get_client(client_id)
        if not client:
            return None

        # Check if email is being updated and if it conflicts
        if client_data.phone and client_data.phone != client.phone:
            existing_client = (
                self.db.query(Client).filter(Client.phone == client_data.phone).first()
            )
            if existing_client:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Client with this phone already exists",
                )

        # Update fields
        update_data = client_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(client, field, value)

        self.db.commit()
        self.db.refresh(client)
        return client

    def delete_client(self, client_id: int) -> bool:
        """Delete a client."""
        client = self.get_client(client_id)
        if not client:
            return False

        self.db.delete(client)
        self.db.commit()
        return True

    def sale_derived_debt(self, client_id: int) -> Decimal:
        """Outstanding debt that comes from this client's unpaid sales."""
        total = (
            self.db.query(func.coalesce(func.sum(Sale.total_amount - Sale.paid_amount), 0))
            .filter(
                Sale.client_id == client_id,
                Sale.status.in_(["debt", "partially_paid"]),
            )
            .scalar()
        )
        return Decimal(total or 0)

    def update_client_debt(
        self, client_id: int, debt_amount: Decimal
    ) -> Optional[Client]:
        """Set a client's total outstanding debt.

        Callers pass the total they want the client to owe. Sale-derived debt
        cannot be edited from here, so the difference is stored as the manual
        adjustment — writing the total straight into debt_amount used to leave
        the debts list unchanged, because that list reads the sales instead.
        """
        client = self.get_client(client_id)
        if not client:
            return None

        target = Decimal(debt_amount)
        client.manual_debt_adjustment = target - self.sale_derived_debt(client_id)
        # Kept in step so anything still reading the column sees the same total.
        client.debt_amount = target
        self.db.commit()
        self.db.refresh(client)
        return client

    def search_clients(
        self, search_term: str, page: int = 1, size: int = 10
    ) -> Tuple[List[Client], dict]:
        """Search clients by name, email, or phone."""
        query = self.db.query(Client).filter(
            or_(
                Client.first_name.ilike(f"%{search_term}%"),
                Client.last_name.ilike(f"%{search_term}%"),
                Client.email.ilike(f"%{search_term}%"),
                Client.phone.ilike(f"%{search_term}%"),
            )
        )

        total = query.count()
        query = paginate_query(query, page, size)
        clients = query.all()

        pagination = calculate_pagination_info(total, page, size)
        return clients, pagination

    def get_clients_with_debt(self) -> List[Client]:
        """Get all clients with outstanding debt."""
        return self.db.query(Client).filter(Client.debt_amount > 0).all()
