from flask import Blueprint, url_for, render_template, request, flash, redirect
from .models import User, Category
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, oauth
from flask_login import login_required, login_user, current_user, logout_user
import os

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['GET', 'POST'])
def login():

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()
        if user:
            if check_password_hash(user.password, password):
                flash('Logged In Successfully!', category='success')
                login_user(user, remember=True)
                return redirect(url_for('views.home'))
            else:
                flash('Email and Password does not match or invalid.', category='error')
        else:
            flash('Email does not exist', category='error')

    
    return render_template('auth.html', user=current_user, mode="login")

@auth.route('/login/google')
def login_google():
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@auth.route('/profile/link-google')
@login_required
def link_google():
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@auth.route('/auth/google/callback')
def google_callback():
    token = oauth.google.authorize_access_token()
    resp = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo")
    user_info = resp.json() if resp else None

    if not user_info:
        flash("Google login failed.", "error")
        return redirect(url_for('auth.login'))

    if not user_info.get("email") or not user_info.get("email_verified"):
        flash("Google email not verified.", "error")
        return redirect(url_for('auth.login'))

    google_id = user_info.get("sub")
    email = user_info.get("email")
    username = user_info.get("name") or email.split("@")[0]

    if current_user.is_authenticated:
        existing = User.query.filter_by(google_id=google_id).first()
        if existing and existing.id != current_user.id:
            flash("That Google account is already linked to another user.", "error")
            return redirect(url_for('auth.profile'))
        current_user.google_id = google_id
        db.session.commit()
        flash("Google account linked!", category="success")
        return redirect(url_for('auth.profile'))

    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id
        else:
            random_pw = generate_password_hash(os.urandom(16).hex(), method='pbkdf2:sha256')
            user = User(
                username=username,
                email=email,
                password=random_pw,
                google_id=google_id
            )
            db.session.add(user)
            db.session.flush()
            def_cat = Category(name="None", user_id=user.id)
            db.session.add(def_cat)

        db.session.commit()

    login_user(user, remember=True)
    flash('Logged In with Google!', category='success')
    return redirect(url_for('views.home'))

@auth.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username')).strip()
        email = (request.form.get('email')).strip()
        password = (request.form.get('password')).strip()
        cpass = (request.form.get('cpass')).strip()

        print("Form Data:", request.form)

# check input data
        if not username or not email or not password or not cpass:
            flash('All fields are required.', category='error')
        elif len(email) < 4:
            flash('Email is too short or invalid.', category='error')
        elif len(username) < 2:
            flash('Username is too short.', category='error')
        elif len(password) < 8:
            flash('Password is too short.', category='error')
        elif password != cpass:
            flash('Passwords do not match.', category='error')
        
        else:
            existing_user = User.query.filter(
                (User.email == email) | (User.username == username)
            ).first()
            if existing_user:
                flash('Email or username already in use.', category='error')
            else:
                new_user = User(
                    username=username,
                    email=email,
                    password=generate_password_hash(password, method='pbkdf2:sha256')
                )
                db.session.add(new_user)
                db.session.commit()

#default category
                def_cat = Category(name="None", user_id=new_user.id)
                db.session.add(def_cat)
                db.session.commit()

                login_user(new_user, remember=True)
                flash('Account Created Successfully!', category='success')

                return redirect(url_for('views.home'))

    return render_template('auth.html', user=current_user, mode="register")


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged Out Successfully!', category='success')
    return redirect(url_for('auth.login'))

@auth.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    if request.method == 'POST':
        form_type = request.form.get('form_type', '')

        if form_type == 'profile_info':
            username = (request.form.get('username') or '').strip()
            email = (request.form.get('email') or '').strip()

            if not username or not email:
                flash("Username and email are required.", "error")
                return redirect(url_for('auth.profile'))

            existing = User.query.filter(
                ((User.email == email) | (User.username == username)) & (User.id != current_user.id)
            ).first()
            if existing:
                flash("Email or username already in use.", "error")
                return redirect(url_for('auth.profile'))

            current_user.username = username
            current_user.email = email
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for('auth.profile'))

        if form_type == 'change_password':
            current_pw = request.form.get('current_password') or ''
            new_pw = request.form.get('new_password') or ''
            confirm_pw = request.form.get('confirm_password') or ''

            if not check_password_hash(current_user.password, current_pw):
                flash("Current password is incorrect.", "error")
                return redirect(url_for('auth.profile'))
            if len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
                return redirect(url_for('auth.profile'))
            if new_pw != confirm_pw:
                flash("Passwords do not match.", "error")
                return redirect(url_for('auth.profile'))

            current_user.password = generate_password_hash(new_pw, method='pbkdf2:sha256')
            db.session.commit()
            flash("Password updated.", "success")
            return redirect(url_for('auth.profile'))

    return render_template('profile.html', user=current_user)
