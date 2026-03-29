# main.py
import os
import requests
import pytesseract
from PIL import Image
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# --- 1. App Initialization & Configuration ---
app = Flask(__name__)
app.secret_key = 'replace_this_with_a_super_secret_key_for_production' # Needed for sessions
# Replace with your actual PostgreSQL credentials
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:8511@localhost:5432/reimbursement_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- 2. Database Models ---
class Company(db.Model):
    __tablename__ = 'companies'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    country = db.Column(db.String(100), nullable=False)
    default_currency = db.Column(db.String(10), nullable=False)
    users = db.relationship('User', backref='company', lazy=True)

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Admin, Manager, Employee
    manager_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_manager_approver = db.Column(db.Boolean, default=True) 

    expenses = db.relationship('Expense', backref='employee', lazy=True)

class Expense(db.Model):
    __tablename__ = 'expenses'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    base_amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, nullable=True)
    date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Approved, Rejected
    receipt_url = db.Column(db.String(255), nullable=True)
    
    approval_steps = db.relationship('ApprovalStep', backref='expense', lazy=True)

class ApprovalStep(db.Model):
    __tablename__ = 'approval_steps'
    id = db.Column(db.Integer, primary_key=True)
    expense_id = db.Column(db.Integer, db.ForeignKey('expenses.id'), nullable=False)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    step_order = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='Pending') # Pending, Approved, Rejected
    comments = db.Column(db.Text, nullable=True)


# --- 3. Utility Functions ---
def get_currency_for_country(country_name):
    try:
        url = "https://restcountries.com/v3.1/all?fields=name,currencies"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            for country_data in data:
                if country_data.get('name', {}).get('common', '').lower() == country_name.lower():
                    currencies = country_data.get('currencies', {})
                    if currencies:
                        return list(currencies.keys())[0] 
    except Exception as e:
        print(f"Error fetching currency: {e}")
    return "USD" 

def convert_currency(amount, from_currency, to_currency):
    if from_currency == to_currency:
        return amount
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{from_currency}"
        response = requests.get(url)
        if response.status_code == 200:
            rates = response.json().get('rates', {})
            rate = rates.get(to_currency, 1)
            return round(amount * rate, 2)
    except Exception as e:
        print(f"Error converting currency: {e}")
    return amount


# --- 4. Application Routes ---

@app.route('/')
def index():
    """Root route redirects to login or dashboard based on session."""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['role'] = user.role
            session['company_id'] = user.company_id
            session['user_name'] = user.name  # Added so the sidebar can display the name
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password', 'error')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """On first login/signup: A new Company and Admin User are auto-created."""
    if request.method == 'POST':
        company_name = request.form.get('company_name')
        country = request.form.get('country')
        admin_name = request.form.get('admin_name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        currency = get_currency_for_country(country)
        
        new_company = Company(name=company_name, country=country, default_currency=currency)
        db.session.add(new_company)
        db.session.flush() 
        
        hashed_pw = generate_password_hash(password)
        admin = User(
            company_id=new_company.id,
            name=admin_name,
            email=email,
            password_hash=hashed_pw,
            role="Admin" 
        )
        db.session.add(admin)
        db.session.commit()
        
        flash('Account created! Please log in.', 'success')
        return redirect(url_for('login'))
        
    return render_template('signup.html')


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    
    if user.role == 'Admin':
        users = User.query.filter_by(company_id=user.company_id).all()
        return render_template('dashboard_admin.html', user=user, users=users)
        
    elif user.role == 'Manager':
        pending_approvals = ApprovalStep.query.filter_by(approver_id=user.id, status='Pending').all()
        return render_template('dashboard_manager.html', user=user, pending_approvals=pending_approvals)
        
    else: # Employee
        my_expenses = Expense.query.filter_by(user_id=user.id).all()
        return render_template('dashboard_employee.html', user=user, expenses=my_expenses)

@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    """Admin page to view and manage users"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    
    if user.role != 'Admin':
        flash('Only Admins can access this page.', 'error')
        return redirect(url_for('dashboard'))
        
    users = User.query.filter_by(company_id=user.company_id).all()
    return render_template('manage_users.html', user=user, users=users)

@app.route('/create_user', methods=['POST'])
def create_user():
    # 1. Security check: Only admins can create users
    if 'user_id' not in session or session.get('role') != 'Admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))

    # 2. Get data from the form
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')  # Capture the password from the form
    role = request.form.get('role', 'Employee')
    manager_id = request.form.get('manager_id')
    
    # The toggle input comes in as a string 'true' or 'false'
    is_approver_str = request.form.get('is_manager_approver', 'false')
    is_manager_approver = (is_approver_str.lower() == 'true')

    # 3. Hash the password provided in the form
    if not password:
        flash('Password is required.', 'error')
        return redirect(url_for('manage_users'))
        
    hashed_password = generate_password_hash(password)

    # 4. Save the new user to the database
    try:
        new_user = User(
            company_id=session.get('company_id'),
            name=name,
            email=email,
            password_hash=hashed_password, # Use the hashed form password
            role=role,
            manager_id=int(manager_id) if manager_id else None,
            is_manager_approver=is_manager_approver
        )
        db.session.add(new_user)
        db.session.commit()
        flash(f'User {name} added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"Database Error: {e}") # Helpful for debugging in your terminal
        flash('Error adding user. Email might already exist.', 'error')

    return redirect(url_for('manage_users'))

@app.route('/delete_user/<int:user_id>', methods=['GET', 'POST'])
def delete_user(user_id):
    """Admin route to delete a user"""
    # 1. Security check: Only admins can delete users
    if 'user_id' not in session or session.get('role') != 'Admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))

    # 2. Find the user in the database
    user_to_delete = User.query.get_or_404(user_id)
    
    # 3. Prevent the admin from deleting themselves
    if user_to_delete.id == session['user_id']:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('manage_users'))

    # 4. Delete the user
    try:
        db.session.delete(user_to_delete)
        db.session.commit()
        flash(f'User {user_to_delete.name} deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error deleting user. They may have expenses tied to their account.', 'error')

    return redirect(url_for('manage_users'))




if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)