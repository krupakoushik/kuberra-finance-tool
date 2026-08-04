from . import db
from flask_login import UserMixin
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.sql import func

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    date_created = db.Column(db.DateTime(timezone=True), server_default=func.now())
    transactions = db.relationship('Transaction', backref='owner', lazy=True)
    categories = db.relationship('Category', backref='owner', lazy=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    transactions = db.relationship('Transaction', backref='category', lazy=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    txn_date = db.Column(db.DateTime(timezone=True), server_default=func.now())
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    txn = db.Column(db.String(6), nullable=False, server_default='DEBIT')
    note = db.Column(db.String, nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)

    __table_args__ = (
        CheckConstraint("txn IN ('CREDIT', 'DEBIT')", name="check_txn_type"),
    )


class Coin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cg_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    coin = db.Column(db.String, nullable=True)
    symbol = db.Column(db.String,  nullable=True)
    holdings = db.relationship('Holding', backref='coin', lazy=True)

class Holding(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    txn_date = db.Column(db.DateTime(timezone=True), server_default=func.now())
    quantity = db.Column(db.Numeric(20, 10), nullable=False, default=0)
    invested = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    price_per_coin = db.Column(db.Numeric(18, 10), nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    coin_id = db.Column(db.Integer, db.ForeignKey('coin.id', ondelete='RESTRICT'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    __table_args__ = (
        UniqueConstraint('coin_id', 'user_id', name='uq_coin_user'),
        CheckConstraint("quantity >= 0", name="check_quantity_non_negative"),
        CheckConstraint("invested >= 0", name="check_invested_non_negative")
    )

class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    side = db.Column(db.String(4), nullable=False)        # BUY / SELL
    quantity = db.Column(db.Numeric(20,10), nullable=False, default=0)
    price_per_coin = db.Column(db.Numeric(18,10), nullable=False, default=0)
    total = db.Column(db.Numeric(18,2),  nullable=False, default=0)
    realized_pnl = db.Column(db.Numeric(18,2), nullable=False, default=0)   # <—
    txn_date = db.Column(db.DateTime(timezone=True), server_default=func.now())
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), index=True, nullable=False)
    coin_id = db.Column(db.Integer, db.ForeignKey('coin.id', ondelete='RESTRICT'), index=True, nullable=False)

class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    month = db.Column(db.Integer, nullable=False)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        db.UniqueConstraint('user_id', 'category_id', 'year', 'month', name='uq_budget_scope'),
        db.CheckConstraint('month BETWEEN 1 AND 12', name='check_budget_month'),
        db.CheckConstraint('amount >= 0', name='check_budget_amount'),
    )

class Savings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    note = db.Column(db.String(150), nullable=False)
    target_amount = db.Column(db.Numeric(18, 2), nullable=False)
    target_date = db.Column(db.Date, nullable=False)
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    asset_type = db.Column(db.String(50), nullable=False)  # e.g., Cash, Stock, Crypto, Real Estate
    value = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    note = db.Column(db.String, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Liability(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    liability_type = db.Column(db.String(50), nullable=False)  # e.g., Credit Card, Loan, Mortgage
    balance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    note = db.Column(db.String, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    updated_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
