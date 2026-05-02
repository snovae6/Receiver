from datetime import datetime
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from sqlmodel import SQLModel, Field, Session, create_engine, select
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///halo_points_compact_tracker.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=True)

app = FastAPI(title="HALO POINTS - Compact Tracker API")


class Deposit(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    log_id: str = Field(index=True, unique=True)
    sender_id: str = Field(index=True)

    item_id: int
    item_name: str
    qty: int

    market_price_each: int
    payout_rate: float
    owed_total: int

    torn_timestamp: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

    paid: bool = False
    paid_at: Optional[datetime] = None


class DepositCreate(SQLModel):
    log_id: str
    sender_id: str
    item_id: int
    item_name: str
    qty: int
    market_price_each: int
    payout_rate: float
    owed_total: int
    torn_timestamp: int


@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)


@app.post("/deposits")
def create_deposit(deposit: DepositCreate):
    with Session(engine) as session:
        existing = session.exec(
            select(Deposit).where(Deposit.log_id == deposit.log_id)
        ).first()

        if existing:
            return {
                "status": "duplicate",
                "deposit_id": existing.id,
                "message": "This Torn log was already processed."
            }

        new_deposit = Deposit.model_validate(deposit)
        session.add(new_deposit)
        session.commit()
        session.refresh(new_deposit)

        return {
            "status": "created",
            "deposit_id": new_deposit.id
        }


@app.get("/deposits", response_model=List[Deposit])
def get_deposits(paid: Optional[bool] = None):
    with Session(engine) as session:
        statement = select(Deposit)

        if paid is not None:
            statement = statement.where(Deposit.paid == paid)

        deposits = session.exec(statement).all()
        return deposits


@app.post("/deposits/{deposit_id}/paid")
def mark_deposit_paid(deposit_id: int):
    with Session(engine) as session:
        deposit = session.get(Deposit, deposit_id)

        if not deposit:
            raise HTTPException(status_code=404, detail="Deposit not found")

        deposit.paid = True
        deposit.paid_at = datetime.utcnow()

        session.add(deposit)
        session.commit()
        session.refresh(deposit)

        return {
            "status": "paid",
            "deposit_id": deposit.id
        }


@app.post("/deposits/mark-sender-paid/{sender_id}")
def mark_sender_paid(sender_id: str):
    with Session(engine) as session:
        statement = select(Deposit).where(
            Deposit.sender_id == sender_id,
            Deposit.paid == False
        )

        deposits = session.exec(statement).all()

        if not deposits:
            return {
                "status": "no_unpaid_deposits",
                "sender_id": sender_id,
                "updated_count": 0
            }

        for deposit in deposits:
            deposit.paid = True
            deposit.paid_at = datetime.utcnow()
            session.add(deposit)

        session.commit()

        return {
            "status": "paid",
            "sender_id": sender_id,
            "updated_count": len(deposits)
        }

@app.delete("/deposits/{deposit_id}")
def delete_deposit(deposit_id: int):
    with Session(engine) as session:
        deposit = session.get(Deposit, deposit_id)

        if not deposit:
            raise HTTPException(status_code=404, detail="Deposit not found")

        session.delete(deposit)
        session.commit()

        return {
            "status": "deleted",
            "deposit_id": deposit_id
        }