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

class PaymentCreate(SQLModel):
    sender_id: str
    amount: int
    note: Optional[str] = None

class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    sender_id: str = Field(index=True)
    amount: int

    note: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class PaymentRequest(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    sender_id: str = Field(index=True)
    amount: int
    note: Optional[str] = None

    status: str = Field(default="pending", index=True)  # pending, approved, declined

    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

class PaymentRequestCreate(SQLModel):
    sender_id: str
    amount: int
    note: Optional[str] = None

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
    
@app.post("/payments")
def create_payment(payment: PaymentCreate):
    if payment.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be greater than 0")

    with Session(engine) as session:
        new_payment = Payment.model_validate(payment)
        session.add(new_payment)
        session.commit()
        session.refresh(new_payment)

        return {
            "status": "created",
            "payment_id": new_payment.id,
            "sender_id": new_payment.sender_id,
            "amount": new_payment.amount
        }


@app.get("/payments", response_model=List[Payment])
def get_payments(sender_id: Optional[str] = None):
    with Session(engine) as session:
        statement = select(Payment)

        if sender_id is not None:
            statement = statement.where(Payment.sender_id == sender_id)

        payments = session.exec(statement).all()
        return payments
    
@app.post("/payment-requests")
def create_payment_request(request: PaymentRequestCreate):
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Request amount must be greater than 0")

    with Session(engine) as session:
        new_request = PaymentRequest.model_validate(request)
        session.add(new_request)
        session.commit()
        session.refresh(new_request)

        return {
            "status": "created",
            "request_id": new_request.id,
            "sender_id": new_request.sender_id,
            "amount": new_request.amount,
            "request_status": new_request.status
        }


@app.get("/payment-requests", response_model=List[PaymentRequest])
def get_payment_requests(
    sender_id: Optional[str] = None,
    status: Optional[str] = None
):
    with Session(engine) as session:
        statement = select(PaymentRequest)

        if sender_id is not None:
            statement = statement.where(PaymentRequest.sender_id == sender_id)

        if status is not None:
            statement = statement.where(PaymentRequest.status == status)

        requests = session.exec(statement).all()
        return requests


@app.patch("/payment-requests/{request_id}/approve")
def approve_payment_request(request_id: int):
    with Session(engine) as session:
        payment_request = session.get(PaymentRequest, request_id)

        if not payment_request:
            raise HTTPException(status_code=404, detail="Payment request not found")

        if payment_request.status != "pending":
            raise HTTPException(status_code=400, detail="Payment request is not pending")

        payment_request.status = "approved"
        payment_request.resolved_at = datetime.utcnow()

        payment = Payment(
            sender_id=payment_request.sender_id,
            amount=payment_request.amount,
            note=f"Approved payment request #{payment_request.id}: {payment_request.note or ''}"
        )

        session.add(payment_request)
        session.add(payment)
        session.commit()
        session.refresh(payment_request)
        session.refresh(payment)

        return {
            "status": "approved",
            "request_id": payment_request.id,
            "payment_id": payment.id,
            "sender_id": payment_request.sender_id,
            "amount": payment_request.amount
        }

@app.patch("/payment-requests/{request_id}/decline")
def decline_payment_request(request_id: int):
    with Session(engine) as session:
        payment_request = session.get(PaymentRequest, request_id)

        if not payment_request:
            raise HTTPException(status_code=404, detail="Payment request not found")

        if payment_request.status != "pending":
            raise HTTPException(status_code=400, detail="Payment request is not pending")

        payment_request.status = "declined"
        payment_request.resolved_at = datetime.utcnow()

        session.add(payment_request)
        session.commit()
        session.refresh(payment_request)

        return {
            "status": "declined",
            "request_id": payment_request.id,
            "sender_id": payment_request.sender_id,
            "amount": payment_request.amount
        }