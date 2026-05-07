import secrets
import requests
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
    sender_name: Optional[str] = None

    amount: int
    note: Optional[str] = None

    status: str = Field(default="pending", index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

class PaymentRequestCreate(SQLModel):
    sender_id: str
    amount: int
    note: Optional[str] = None

class SenderVerifyRequest(SQLModel):
    api_key: str


class VerifiedPaymentRequestCreate(SQLModel):
    token: str
    amount: int
    note: Optional[str] = None

class SenderSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    token: str = Field(index=True, unique=True)
    sender_id: str = Field(index=True)
    torn_name: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

def verify_torn_api_key(api_key: str):
    url = f"https://api.torn.com/v2/user?selections=basic&key={api_key}"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Could not verify Torn API key")

    if data.get("error"):
        raise HTTPException(status_code=400, detail=data["error"].get("error", "Invalid Torn API key"))

    # Torn v2/basic commonly returns id/name at top level
    torn_id = (
        data.get("id")
        or data.get("player_id")
        or data.get("profile", {}).get("id")
        or data.get("user", {}).get("id")
    )

    torn_name = (
        data.get("name")
        or data.get("profile", {}).get("name")
        or data.get("user", {}).get("name")
    )

    print("TORN VERIFY RESPONSE:", data)

    if not torn_id:
        raise HTTPException(status_code=400, detail="Could not determine Torn ID from API key")

    return str(torn_id), torn_name

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
    
@app.post("/payment-requests/{request_id}/approve")
def approve_payment_request_post(request_id: int):
    return approve_payment_request(request_id)


@app.post("/payment-requests/{request_id}/decline")
def decline_payment_request_post(request_id: int):
    return decline_payment_request(request_id)

@app.post("/sender/verify")
def verify_sender(request: SenderVerifyRequest):
    sender_id, torn_name = verify_torn_api_key(request.api_key)

    token = secrets.token_hex(32)

    with Session(engine) as session:
        sender_session = SenderSession(
            token=token,
            sender_id=sender_id,
            torn_name=torn_name
        )

        session.add(sender_session)
        session.commit()
        session.refresh(sender_session)

        return {
            "status": "verified",
            "token": token,
            "sender_id": sender_id,
            "torn_name": torn_name
        }


@app.get("/ledger/verified/{token}")
def get_verified_sender_ledger(token: str):
    with Session(engine) as session:
        sender_session = session.exec(
            select(SenderSession).where(SenderSession.token == token)
        ).first()

        if not sender_session:
            raise HTTPException(status_code=401, detail="Invalid sender verification token")

        return get_sender_ledger(sender_session.sender_id)


@app.post("/payment-requests/verified")
def create_verified_payment_request(request: VerifiedPaymentRequestCreate):
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="Request amount must be greater than 0")

    ledger = get_sender_ledger(sender_session.sender_id)

    if request.amount > ledger["balance"]:
        raise HTTPException(status_code=400, detail="Request exceeds available balance")

    with Session(engine) as session:
        sender_session = session.exec(
            select(SenderSession).where(SenderSession.token == request.token)
        ).first()

        if not sender_session:
            raise HTTPException(status_code=401, detail="Invalid sender verification token")

        new_request = PaymentRequest(
            sender_id=sender_session.sender_id,
            sender_name=sender_session.torn_name,
            amount=request.amount,
            note=request.note
        )

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

@app.get("/ledger/{sender_id}")
def get_sender_ledger(sender_id: str):
    with Session(engine) as session:
        deposits = session.exec(
            select(Deposit).where(
                Deposit.sender_id == sender_id,
                Deposit.paid == False
            )
        ).all()

        payments = session.exec(
            select(Payment).where(Payment.sender_id == sender_id)
        ).all()

        requests = session.exec(
            select(PaymentRequest).where(PaymentRequest.sender_id == sender_id)
        ).all()

        total_deposits = sum(d.owed_total for d in deposits)
        total_payments = sum(p.amount for p in payments)
        balance = max(total_deposits - total_payments, 0)

        return {
            "sender_id": sender_id,
            "total_deposits": total_deposits,
            "total_payments": total_payments,
            "balance": balance,
            "deposits": deposits,
            "payments": payments,
            "payment_requests": requests
        }