from flask import Blueprint, render_template, url_for, request, flash, redirect, current_app, jsonify
from flask_login import login_required, current_user
from .models import User, Transaction, Category, Coin, Holding, Trade, Asset, Liability
from datetime import datetime, date
from . import db
from dotenv import load_dotenv
import os, requests
from sqlalchemy import func, case
from decimal import Decimal
from time import time
from collections import deque
from calendar import monthrange

_CACHE = {}
def cache_get(key):
    item = _CACHE.get(key)
    if item and item["exp"] > time():
        return item["val"]
    return None

def cache_set(key, val, ttl=60):
    _CACHE[key] = {"val": val, "exp": time() + ttl}

CG_PRO = "https://pro-api.coingecko.com/api/v3"
CG_PUB = "https://api.coingecko.com/api/v3"

def cg_get_json(url, headers=None, params=None, ttl=60, fallback_url=None):
    key = (url, tuple(sorted((params or {}).items())))
    hit = cache_get(key)
    if hit is not None:
        return hit

    r = None
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        data = r.json()
    except Exception:
        data = None

    ok = (r is not None and r.status_code == 200) and isinstance(data, (dict, list))

    if not ok and fallback_url:
        try:
            r2 = requests.get(fallback_url, params=params, timeout=10)
            data2 = r2.json()
            ok = (r2.status_code == 200) and isinstance(data2, (dict, list))
            if ok: data = data2
        except Exception:
            ok = False

    if ok:
        cache_set(key, data, ttl)
        return data

    if url.endswith("/coins/markets"):
        return []
    return {"coins": []}


def fifo_pnl_for_coin(trades, current_price: Decimal):
    lots = deque()
    realized = Decimal("0")

    for t in trades:
        qty = Decimal(t.quantity)
        unit_cost = (Decimal(t.total) / qty) if qty > 0 else Decimal("0")

        if t.side == "BUY":
            lots.append([qty, unit_cost])
        else:  
            to_sell = qty
            basis = Decimal("0")
            proceeds = Decimal(t.total)
            while to_sell > 0 and lots:
                lot_qty, lot_cost = lots[0]
                take = min(to_sell, lot_qty)
                basis += take * lot_cost
                lot_qty -= take
                to_sell -= take
                if lot_qty == 0:
                    lots.popleft()
                else:
                    lots[0][0] = lot_qty
            realized += proceeds - basis

    qty_rem = sum(q for q, _ in lots) if lots else Decimal("0")
    invested = sum(q * c for q, c in lots) if lots else Decimal("0")
    value = qty_rem * current_price
    unrealized = sum(q * (current_price - c) for q, c in lots) if lots else Decimal("0")
    pnl = realized + unrealized
    pnl_pct = (pnl / invested * 100) if invested > 0 else None

    return {
        "qty": qty_rem, "invested": invested, "realized": realized,
        "unrealized": unrealized, "value": value, "pnl": pnl, "pnl_pct": pnl_pct
    }


def fifo_realized_by_trade(trades):
    lots = deque()
    out = {}

    for t in trades:
        qty = Decimal(t.quantity)
        unit_cost = (Decimal(t.total) / qty) if qty > 0 else Decimal("0")

        if t.side == "BUY":
            lots.append([qty, unit_cost])
        else:
            to_sell = qty
            basis = Decimal("0")
            proceeds = Decimal(t.total)
            while to_sell > 0 and lots:
                lot_qty, lot_cost = lots[0]
                take = min(to_sell, lot_qty)
                basis += take * lot_cost
                lot_qty -= take
                to_sell -= take
                if lot_qty == 0:
                    lots.popleft()
                else:
                    lots[0][0] = lot_qty
            out[t.id] = proceeds - basis

    return out

def recompute_holding_from_trades(user_id, coin_id):
    remaining = (Trade.query
        .filter_by(user_id=user_id, coin_id=coin_id)
        .order_by(Trade.txn_date.asc(), Trade.id.asc())
        .all())

    h = Holding.query.filter_by(user_id=user_id, coin_id=coin_id).first()
    if not h:
        h = Holding(user_id=user_id, coin_id=coin_id, quantity=0, invested=0, price_per_coin=0)
        db.session.add(h)

    qty = Decimal("0")
    invested = Decimal("0")
    for t in remaining:
        t_qty = Decimal(t.quantity)
        if t.side == "BUY":
            qty += t_qty
            invested += Decimal(t.total)
        else:
            avg_cost = (invested / qty) if qty > 0 else Decimal("0")
            qty = max(Decimal("0"), qty - t_qty)
            invested = max(Decimal("0"), invested - (avg_cost * t_qty))

    h.quantity = qty
    h.invested = invested
    h.price_per_coin = (invested / qty) if qty > 0 else Decimal(0)
    return h

load_dotenv()
views = Blueprint('views', __name__)

@views.route('/', methods=['GET', 'POST'])
@login_required
def home():
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.txn_date.desc()).all()

    balance = sum(
        t.amount if t.txn == "CREDIT" else -t.amount
        for t in transactions
    )

    if request.method == 'POST':
        form_type = request.form.get('form_type')

        # ---- Add / Edit Transaction ----
        if form_type == "transaction":
            transaction_id = request.form.get('transaction_id')
            category_id = request.form.get('category_id')

            # --- QUICK CATEGORY UPDATE (inline form) ---
            if transaction_id and category_id and not request.form.get('amount'):
                txn_obj = Transaction.query.get_or_404(transaction_id)
                if txn_obj.user_id != current_user.id:
                    flash("Not authorized to edit this transaction!", category='error')
                    return redirect(url_for('views.home'))

                txn_obj.category_id = int(category_id)
                db.session.commit()
                flash("Category updated!", category="success")
                return redirect(url_for('views.home'))

            # --- FULL ADD/EDIT TRANSACTION ---
            txn_date = request.form.get('date')
            txn_type = request.form.get('txn')
            amount = request.form.get('amount')
            note = request.form.get('note')

            try:
                amount = float(amount)
            except (ValueError, TypeError):
                flash("Invalid Amount!", category='error')
                return redirect(url_for('views.home'))

            try:
                txn_date = datetime.strptime(txn_date, "%Y-%m-%d") if txn_date else None
            except ValueError:
                flash("Invalid date format. Use YYYY-MM-DD.", category="error")
                return redirect(url_for('views.home'))

            category_id = int(category_id) if category_id else None

            if transaction_id:  # Editing
                txn_obj = Transaction.query.get_or_404(transaction_id)
                if txn_obj.user_id != current_user.id:
                    flash("Not authorized to edit this transaction!", category='error')
                    return redirect(url_for('views.home'))

                txn_obj.txn_date = txn_date
                txn_obj.txn = txn_type
                txn_obj.amount = amount
                txn_obj.category_id = category_id
                txn_obj.note = note
                flash("Transaction updated!", category="success")
            else:  # Adding
                new_txn = Transaction(
                    txn_date=txn_date,
                    amount=amount,
                    txn=txn_type,
                    category_id=category_id,
                    note=note,
                    user_id=current_user.id
                )
                db.session.add(new_txn)
                flash("Transaction added!", category="success")

            db.session.commit()
            return redirect(url_for('views.home'))


        # ---- Add / Rename Category ----
        elif form_type == "category":
            category_id = request.form.get('category_idd')
            category_name = request.form.get('category', '').strip()

            if not category_name:
                flash("Category name cannot be empty.", category="error")
                return redirect(url_for('views.home'))
            
            existing = Category.query.filter_by(user_id=current_user.id, name=category_name).first()
            if existing and (not category_id or existing.id != int(category_id)):
                flash("Category name already exists.", "error")
                return redirect(url_for('views.home'))


            if category_id:  # Rename
                cat = Category.query.get_or_404(category_id)
                if cat.user_id != current_user.id:
                    flash("Not authorized to rename this category!", category='error')
                    return redirect(url_for('views.home'))

                cat.name = category_name
                print(category_name)
                flash("Category renamed successfully!", category="success")
            else:  # Add
                new_cat = Category(name=category_name, user_id=current_user.id)
                db.session.add(new_cat)
                print(new_cat)
                flash("Category created successfully!", category="success")

            db.session.commit()
            return redirect(url_for('views.home'))

    categories = Category.query.filter_by(user_id=current_user.id).all()
    return render_template(
        'transactions.html',
        transaction=transactions,
        current_date=date.today().isoformat(),
        categories=categories,
        balance=balance,
        user=current_user
    )


@views.route('/cdelete/<int:id>')
@login_required
def categoriesdel(id):
    cat = Category.query.get_or_404(id)
    if cat.user_id != current_user.id:
        flash("You are not allowed to delete this category!", category='error')
        return redirect(url_for('views.home'))
    db.session.delete(cat)
    db.session.commit()
    flash("Category deleted successfully!", category='success')
    return redirect(url_for('views.home'))

@views.route('/delete/<int:id>')
@login_required
def delete(id):
    del_txn = Transaction.query.get_or_404(id)
    if del_txn.user_id != current_user.id:
        flash("You are not allowed to delete this transaction!", category='error')
        return redirect(url_for('views.home'))
    db.session.delete(del_txn)
    db.session.commit()
    flash("Transaction deleted successfully!", category='success')
    return redirect(url_for('views.home'))


@views.route('/portfolio', methods=['GET', 'POST'])
@login_required
def portfolio():
    headers = {"accept": "application/json"}
    api_key = os.getenv("CG_API_KEY", "").strip()
    if api_key:
        headers["x-cg-pro-api-key"] = api_key

    # POST: add selected coins
    if request.method == 'POST':
        cg_ids = request.form.getlist('cg_ids')
        for cg_id in cg_ids:
            coin = Coin.query.filter_by(cg_id=cg_id).first()
            if not coin:
                j = cg_get_json(f"{CG_PRO}/coins/{cg_id}",
                                headers=headers,
                                params={"localization": "false"},
                                ttl=3600,
                                fallback_url=f"{CG_PUB}/coins/{cg_id}")
                coin = Coin(cg_id=cg_id, coin=j.get("name"), symbol=j.get("symbol"))
                db.session.add(coin); db.session.flush()

            if not Holding.query.filter_by(user_id=current_user.id, coin_id=coin.id).first():
                db.session.add(Holding(quantity=0, invested=0, user_id=current_user.id, coin_id=coin.id))
        db.session.commit()
        flash("Crypto added to your portfolio!", "success")
        return redirect(url_for('views.portfolio'))

    # Modal list (search or trending)
    q = request.args.get('q', '').strip()
    if q:
        resp = cg_get_json(f"{CG_PRO}/search", headers=headers, params={"query": q}, ttl=120, fallback_url=f"{CG_PUB}/search")
        list_coins = resp.get("coins", [])
    else:
        resp = cg_get_json(f"{CG_PRO}/search/trending", headers=headers, ttl=120, fallback_url=f"{CG_PUB}/search/trending")
        list_coins = [c.get("item", {}) for c in resp.get("coins", []) if isinstance(c, dict)]

    # Market data for holdings
    hold = Holding.query.filter_by(user_id=current_user.id).all()
    ids = [h.coin.cg_id for h in hold if h.coin and h.coin.cg_id]
    market_rows = []
    if ids:
        jr = cg_get_json(f"{CG_PRO}/coins/markets",
                        headers=headers,
                        params={"vs_currency": "inr", "ids": ",".join(ids), "price_change_percentage": "24h"},
                        ttl=60,
                        fallback_url=f"{CG_PUB}/coins/markets")
        market_rows = jr if isinstance(jr, list) else []

    # Normalize + index
    coins_map = {}
    for row in (market_rows or []):
        if not isinstance(row, dict):
            continue
        cid = row.get("id")
        if not cid:
            continue
        try:  row["current_price"] = Decimal(str(row.get("current_price") or 0))
        except: row["current_price"] = Decimal(0)
        try:  row["price_change_percentage_24h"] = float(row.get("price_change_percentage_24h") or 0)
        except: row["price_change_percentage_24h"] = 0.0

        try:
            raw_rank = row.get("market_cap_rank")
            row["market_cap_rank"] = int(raw_rank) if raw_rank is not None else None
        except:
            row["market_cap_rank"] = None

        row["market_cap_rank_display"] = row["market_cap_rank"] if row["market_cap_rank"] is not None else "-"
        coins_map[cid] = row

    # ---- balance (current market value) ----
    balance = Decimal(0)
    for h in hold:
        r = coins_map.get(h.coin.cg_id)
        if r:
            balance += h.quantity * r["current_price"]

    # all trades for this user, grouped by coin
    user_trades = (
        db.session.query(Trade)
        .filter(Trade.user_id == current_user.id)
        .order_by(Trade.txn_date.asc(), Trade.id.asc())
        .all()
    )
    trades_by_coin = {}
    for t in user_trades:
        trades_by_coin.setdefault(t.coin_id, []).append(t)

    calc_map = {}

    for h in hold:
        row = coins_map.get(h.coin.cg_id)
        if not row:
            continue
        cur_price = Decimal(row["current_price"])
        tlist = trades_by_coin.get(h.coin_id, [])
        calc = fifo_pnl_for_coin(tlist, cur_price)
        calc_map[h.coin_id] = calc

    # Portfolio totals based on FIFO
    current_balance = sum(c["value"] for c in calc_map.values())
    total_invested  = sum(c["invested"] for c in calc_map.values())
    realized_total  = sum(c["realized"] for c in calc_map.values())
    unrealized_total = sum(c["unrealized"] for c in calc_map.values())
    total_pnl       = realized_total + unrealized_total
    total_pnl_pct   = (total_pnl / total_invested * 100) if total_invested else None


    return render_template(
        "portfolio.html",
        coins=list_coins,
        coins_map=coins_map,
        crypto=hold,
        user=current_user,
        balance=current_balance,
        total_invested=total_invested,
        unrealized_total=unrealized_total,
        realized_total=realized_total,
        total_pnl=total_pnl,
        total_pnl_pct=total_pnl_pct,
        calc_map=calc_map,
        market_rows=market_rows,
    )


@views.route('/portfolio/trade', methods=['POST'])
@login_required
def trade():
    from datetime import datetime as dt
    form_type = request.form.get('form_type', '')
    coin_id = int(request.form['coin_id'])

    qty  = Decimal(str(request.form.get('quantity', '0')))
    ppc  = Decimal(str(request.form.get('price_per_coin', '0')))
    dt_str = request.form.get('datetime') or request.form.get('date')
    tx_dt = None
    if dt_str:
        try:
            tx_dt = dt.fromisoformat(dt_str)
        except Exception:
            flash("Invalid date & time.", "error")
            return redirect(url_for('views.portfolio'))

    if form_type == "crypto_buy":
        total = Decimal(str(request.form.get('total_spent', '0')))
        if qty <= 0 or total < 0:
            flash("Quantity and amount must be non-negative.", "error")
            return redirect(url_for('views.portfolio'))

        trade = Trade(
            user_id=current_user.id, 
            coin_id=coin_id,
            side='BUY', 
            quantity=qty, 
            price_per_coin=ppc,
            total=total, 
            txn_date=tx_dt
        )
        db.session.add(trade)

        h = Holding.query.filter_by(user_id=current_user.id, coin_id=coin_id).first()
        if not h:
            h = Holding(user_id=current_user.id, coin_id=coin_id, quantity=0, invested=0, price_per_coin=0)
            db.session.add(h)

        h.quantity += qty
        h.invested += total
        h.price_per_coin = (h.invested / h.quantity) if h.quantity > 0 else Decimal(0)
        if tx_dt: h.txn_date = tx_dt

        db.session.commit()
        flash("Buy recorded!", "success")

    elif form_type == "crypto_sell":
        total_received = Decimal(str(request.form.get('total_received', '0')))
        if qty <= 0 or total_received < 0:
            flash("Quantity and amount must be non-negative.", "error")
            return redirect(url_for('views.portfolio'))

        h = Holding.query.filter_by(user_id=current_user.id, coin_id=coin_id).first()
        if not h or h.quantity < qty:
            flash("Not enough holdings to sell.", "error")
            return redirect(url_for('views.portfolio'))

        # avg cost BEFORE mutating the holding
        avg_cost = (h.invested / h.quantity) if h.quantity > 0 else Decimal(0)
        realized = total_received - (avg_cost * qty)

        trade = Trade(
            user_id=current_user.id, coin_id=coin_id, side='SELL',
            quantity=qty, price_per_coin=ppc, total=total_received,
            realized_pnl=realized, txn_date=tx_dt
        )
        db.session.add(trade)

        h.quantity -= qty
        h.invested = max(Decimal(0), h.invested - (avg_cost * qty))
        h.price_per_coin = (h.invested / h.quantity) if h.quantity > 0 else Decimal(0)
        if tx_dt: h.txn_date = tx_dt
        if h.quantity == 0:
            h.invested = Decimal(0); h.price_per_coin = Decimal(0)

        db.session.commit()
        flash("Sell recorded!", "success")


    else:
        flash("Unknown trade type.", "error")

    return redirect(url_for('views.portfolio'))

@views.route('/portfolio/remove_coin/<coin_id>', methods=['POST'])
@login_required
def remove_coin(coin_id):
    holdings = Holding.query.filter_by(user_id=current_user.id, coin_id=coin_id).all()
    trades = Trade.query.filter_by(user_id=current_user.id, coin_id=coin_id).all()
    if holdings:
        for h in holdings:
            db.session.delete(h)
        for t in trades:
            db.session.delete(t)
        db.session.commit()
        flash("Coin removed from portfolio.", "success")
    else:
        flash("Coin not found in your portfolio.", "error")
    return redirect(url_for('views.portfolio'))

@views.route('/portfolio/trades/<int:coin_id>')
@login_required
def trades(coin_id):
    coin = Coin.query.get_or_404(coin_id)

    trades_asc = (Trade.query
        .filter_by(user_id=current_user.id, coin_id=coin_id)
        .order_by(Trade.txn_date.asc(), Trade.id.asc())
        .all())

    fifo_realized_map = fifo_realized_by_trade(trades_asc)

    trades_desc = (Trade.query
        .filter_by(user_id=current_user.id, coin_id=coin_id)
        .order_by(Trade.txn_date.desc(), Trade.id.desc())
        .all())

    return render_template(
        "trades.html",
        trades=trades_desc,
        coin=coin,
        user=current_user,
        fifo_realized=fifo_realized_map,
    )

@views.route('/portfolio/trades/<int:coin_id>/delete/<int:trade_id>', methods=['POST'])
@login_required
def delete_trade(coin_id, trade_id):
    trade = Trade.query.get_or_404(trade_id)
    if trade.user_id != current_user.id:
        flash("You are not authorized to delete this trade!", category='error')
        return redirect(url_for('views.portfolio'))

    db.session.delete(trade)
    db.session.flush()
    recompute_holding_from_trades(current_user.id, coin_id)
    db.session.commit()
    flash("Trade deleted successfully!", "success")
    return redirect(url_for('views.trades', coin_id=trade.coin_id))

@views.route('/portfolio/trades/<int:coin_id>/edit/<int:trade_id>', methods=['POST'])
@login_required
def edit_trade(coin_id, trade_id):
    from datetime import datetime as dt
    trade = Trade.query.get_or_404(trade_id)
    if trade.user_id != current_user.id:
        flash("You are not authorized to edit this trade!", category='error')
        return redirect(url_for('views.portfolio'))

    side = request.form.get('side', trade.side).upper()
    qty = Decimal(str(request.form.get('quantity', trade.quantity)))
    ppc = Decimal(str(request.form.get('price_per_coin', trade.price_per_coin)))
    total = Decimal(str(request.form.get('total', trade.total)))
    dt_str = request.form.get('datetime') or request.form.get('date')

    if side not in ("BUY", "SELL"):
        flash("Invalid trade type.", "error")
        return redirect(url_for('views.trades', coin_id=coin_id))
    if qty <= 0 or total < 0:
        flash("Quantity and amount must be non-negative.", "error")
        return redirect(url_for('views.trades', coin_id=coin_id))

    tx_dt = trade.txn_date
    if dt_str:
        try:
            tx_dt = dt.fromisoformat(dt_str)
        except Exception:
            flash("Invalid date & time.", "error")
            return redirect(url_for('views.trades', coin_id=coin_id))

    trade.side = side
    trade.quantity = qty
    trade.price_per_coin = ppc
    trade.total = total
    trade.txn_date = tx_dt

    recompute_holding_from_trades(current_user.id, coin_id)
    db.session.commit()
    flash("Trade updated successfully!", "success")
    return redirect(url_for('views.trades', coin_id=coin_id))

@views.route('/api/stats')
@login_required
def api_stats():
    def to_float(x):
        try:
            return float(x)
        except Exception:
            return 0.0

    today = date.today()
    month_start = date(today.year, today.month, 1)
    next_month = date(today.year + (1 if today.month == 12 else 0), 1 if today.month == 12 else today.month + 1, 1)

    # Category spend (this month)
    month_txns = (Transaction.query
        .filter(Transaction.user_id == current_user.id)
        .filter(Transaction.txn == "DEBIT")
        .filter(Transaction.txn_date >= month_start)
        .filter(Transaction.txn_date < next_month)
        .all())

    cat_totals = {}
    cat_map = {}
    for c in Category.query.filter_by(user_id=current_user.id).all():
        cat_map[c.name] = c.id

    for t in month_txns:
        cat_name = t.category.name if t.category else "Uncategorized"
        cat_totals[cat_name] = cat_totals.get(cat_name, 0) + to_float(t.amount)

    cat_labels = list(cat_totals.keys())
    cat_values = [cat_totals[k] for k in cat_labels]

    # Monthly spend (last 6 months)
    def add_months(d, n):
        y = d.year + (d.month - 1 + n) // 12
        m = (d.month - 1 + n) % 12 + 1
        day = min(d.day, monthrange(y, m)[1])
        return date(y, m, day)

    start_6 = add_months(month_start, -5)
    end_6 = add_months(month_start, 1)

    six_txns = (Transaction.query
        .filter(Transaction.user_id == current_user.id)
        .filter(Transaction.txn == "DEBIT")
        .filter(Transaction.txn_date >= start_6)
        .filter(Transaction.txn_date < end_6)
        .all())

    monthly_totals = {}
    for t in six_txns:
        if not t.txn_date:
            continue
        key = t.txn_date.strftime("%Y-%m")
        monthly_totals[key] = monthly_totals.get(key, 0) + to_float(t.amount)

    month_labels = []
    month_values = []
    cur = start_6
    for _ in range(6):
        k = cur.strftime("%Y-%m")
        month_labels.append(k)
        month_values.append(monthly_totals.get(k, 0))
        cur = add_months(cur, 1)

    # Crypto allocation (cost basis)
    items = []
    crypto_total = 0.0
    holdings = Holding.query.filter_by(user_id=current_user.id).all()
    for h in holdings:
        if not h.coin:
            continue
        value = to_float(h.quantity) * to_float(h.price_per_coin)
        items.append({"coin": h.coin.coin or h.coin.symbol or "Unknown", "value": value})
        crypto_total += value

    return jsonify({
        "categories": {"labels": cat_labels, "values": cat_values, "map": cat_map},
        "monthly": {"labels": month_labels, "values": month_values},
        "crypto": {"items": items, "total": crypto_total},
    })

@views.route('/stats')
@login_required
def stats():
    return render_template('dashboard.html', user=current_user)


@views.route('/goal')
@login_required
def goals():
    pass


@views.route('/assets', methods=['GET', 'POST'])
@login_required
def assets():
    if request.method == 'POST':
        form_type = request.form.get('form_type', '')

        if form_type == 'asset':
            asset_id = request.form.get('asset_id')
            name = (request.form.get('name') or '').strip()
            asset_type = (request.form.get('asset_type') or '').strip()
            value_raw = request.form.get('value')
            note = request.form.get('note')

            try:
                value = float(value_raw)
            except (TypeError, ValueError):
                flash("Invalid asset value.", "error")
                return redirect(url_for('views.assets'))

            if not name or not asset_type:
                flash("Asset name and type are required.", "error")
                return redirect(url_for('views.assets'))

            if asset_id:
                a = Asset.query.get_or_404(asset_id)
                if a.user_id != current_user.id:
                    flash("Not authorized.", "error")
                    return redirect(url_for('views.assets'))
                a.name = name
                a.asset_type = asset_type
                a.value = value
                a.note = note
                flash("Asset updated.", "success")
            else:
                a = Asset(name=name, asset_type=asset_type, value=value, note=note, user_id=current_user.id)
                db.session.add(a)
                flash("Asset added.", "success")

            db.session.commit()
            return redirect(url_for('views.assets'))

        if form_type == 'liability':
            liab_id = request.form.get('liability_id')
            name = (request.form.get('name') or '').strip()
            liability_type = (request.form.get('liability_type') or '').strip()
            balance_raw = request.form.get('balance')
            note = request.form.get('note')

            try:
                balance = float(balance_raw)
            except (TypeError, ValueError):
                flash("Invalid liability balance.", "error")
                return redirect(url_for('views.assets'))

            if not name or not liability_type:
                flash("Liability name and type are required.", "error")
                return redirect(url_for('views.assets'))

            if liab_id:
                l = Liability.query.get_or_404(liab_id)
                if l.user_id != current_user.id:
                    flash("Not authorized.", "error")
                    return redirect(url_for('views.assets'))
                l.name = name
                l.liability_type = liability_type
                l.balance = balance
                l.note = note
                flash("Liability updated.", "success")
            else:
                l = Liability(name=name, liability_type=liability_type, balance=balance, note=note, user_id=current_user.id)
                db.session.add(l)
                flash("Liability added.", "success")

            db.session.commit()
            return redirect(url_for('views.assets'))

    assets = Asset.query.filter_by(user_id=current_user.id).order_by(Asset.updated_at.desc()).all()
    liabilities = Liability.query.filter_by(user_id=current_user.id).order_by(Liability.updated_at.desc()).all()

    total_assets = sum(float(a.value) for a in assets) if assets else 0
    total_liabilities = sum(float(l.balance) for l in liabilities) if liabilities else 0
    net_worth = total_assets - total_liabilities

    return render_template(
        "assets.html",
        user=current_user,
        assets=assets,
        liabilities=liabilities,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        net_worth=net_worth,
    )

@views.route('/assets/delete/asset/<int:asset_id>', methods=['POST'])
@login_required
def delete_asset(asset_id):
    a = Asset.query.get_or_404(asset_id)
    if a.user_id != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for('views.assets'))
    db.session.delete(a)
    db.session.commit()
    flash("Asset deleted.", "success")
    return redirect(url_for('views.assets'))

@views.route('/assets/delete/liability/<int:liability_id>', methods=['POST'])
@login_required
def delete_liability(liability_id):
    l = Liability.query.get_or_404(liability_id)
    if l.user_id != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for('views.assets'))
    db.session.delete(l)
    db.session.commit()
    flash("Liability deleted.", "success")
    return redirect(url_for('views.assets'))

