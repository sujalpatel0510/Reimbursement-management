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
    status = db.Column(db.String(20), default='Pending') 
    comments = db.Column(db.Text, nullable=True)
    
    approver = db.relationship('User', foreign_keys=[approver_id])

class ApprovalRule(db.Model):
    __tablename__ = 'approval_rules'
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    target_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    manager_first = db.Column(db.Boolean, default=True)
    percentage_threshold = db.Column(db.Integer, nullable=True)
    specific_approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    is_sequential = db.Column(db.Boolean, default=True)

    steps = db.relationship('ApprovalRuleStep', backref='rule', lazy=True, cascade="all, delete-orphan")
    target_user = db.relationship('User', foreign_keys=[target_user_id])
    specific_approver = db.relationship('User', foreign_keys=[specific_approver_id])

class ApprovalRuleStep(db.Model):
    __tablename__ = 'approval_rule_steps'
    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey('approval_rules.id'), nullable=False)
    approver_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    step_order = db.Column(db.Integer, nullable=False)
    
    approver = db.relationship('User', foreign_keys=[approver_id])

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



@app.route('/submit_expense', methods=['GET', 'POST'])
def submit_expense():
    """Employee can submit expense claims"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        amount = float(request.form.get('amount'))
        currency = request.form.get('currency')
        category = request.form.get('category')
        description = request.form.get('description')
        date_str = request.form.get('date')
        
        company = Company.query.get(user.company_id)
        base_amount = convert_currency(amount, currency, company.default_currency)
        
        new_expense = Expense(
            user_id=user.id,
            amount=amount,
            currency=currency,
            base_amount=base_amount,
            category=category,
            description=description,
            date=datetime.strptime(date_str, '%Y-%m-%d').date()
        )
        db.session.add(new_expense)
        db.session.flush()
        
        if user.manager_id and user.is_manager_approver:
            step1 = ApprovalStep(
                expense_id=new_expense.id, 
                approver_id=user.manager_id, 
                step_order=1
            )
            db.session.add(step1)
            
        db.session.commit()
        flash('Expense submitted successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('submit_expense.html', user=user)

@app.route('/approve_expense/<int:step_id>', methods=['POST'])
def approve_expense(step_id):
    """Manager approves or rejects expenses"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    step = ApprovalStep.query.get_or_404(step_id)
    
    if step.approver_id != session['user_id']:
        flash('Unauthorized action.', 'error')
        return redirect(url_for('dashboard'))
        
    action = request.form.get('action') # "Approve" or "Reject"
    comments = request.form.get('comments', '')
    
    step.status = 'Approved' if action == 'Approve' else 'Rejected'
    step.comments = comments
    
    expense = Expense.query.get(step.expense_id)
    
    if action == 'Reject':
        expense.status = 'Rejected'
    elif action == 'Approve':
        next_steps = ApprovalStep.query.filter_by(expense_id=expense.id, status='Pending').count()
        if next_steps == 0:
            expense.status = 'Approved'
            
    db.session.commit()
    flash(f'Expense {action.lower()}d.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/review_expense/<int:step_id>')
def review_expense(step_id):
    """Detailed view for a manager to review an expense before approving/rejecting."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    # Fetch the specific approval step
    step = ApprovalStep.query.get_or_404(step_id)
    
    # Security check: Only the assigned approver (or an Admin) can view this specific review page
    if step.approver_id != session.get('user_id') and session.get('role') != 'Admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))
        
    return render_template('review_expense.html', step=step)


@app.route('/all_expenses')
def all_expenses():
    if 'user_id' not in session or session.get('role') != 'Admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))
    
    # Fetch all expenses across the company
    expenses = Expense.query.filter_by(company_id=session.get('company_id')).all()
    return render_template('all_expenses.html', expenses=expenses)

@app.route('/pending_approvals')
def pending_approvals():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # We use .join(Expense) to make sure the expense data is loaded 
    # and ready for the HTML table
    pending = ApprovalStep.query.join(Expense).filter(
        ApprovalStep.approver_id == session.get('user_id'),
        ApprovalStep.status == 'Pending'
    ).all()
    
    # We also need to pass 'user' so the sidebar and name show up correctly
    current_user = User.query.get(session.get('user_id'))
    
    return render_template('dashboard_manager.html', 
                           pending_approvals=pending, 
                           user=current_user)

@app.route('/team_expenses')
def team_expenses():
    # 1. Security Check: Only Admins and Managers can view this page
    if 'user_id' not in session or session.get('role') not in ['Admin', 'Manager']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))
    
    # 2. Fetch the right expenses based on the user's role
    if session.get('role') == 'Admin':
        # Admins can see every expense in the company
        expenses = Expense.query.filter_by(company_id=session.get('company_id')).all()
    else:
        # Managers only see expenses from their direct reports
        subordinates = User.query.filter_by(manager_id=session.get('user_id')).all()
        
        # Extract just the ID numbers of the subordinates
        sub_ids = [s.id for s in subordinates]
        
        # Fetch expenses where the submitter's ID is in our list of subordinates
        expenses = Expense.query.filter(Expense.user_id.in_(sub_ids)).all()
        
    # 3. Send the data to the HTML template
    return render_template('all_expenses.html', expenses=expenses)


def team_expenses():
    if 'user_id' not in session or session.get('role') not in ['Admin', 'Manager']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))
    
    # Admins see all, Managers see their direct reports
    if session.get('role') == 'Admin':
        expenses = Expense.query.filter_by(company_id=session.get('company_id')).all()
    else:
        # Get list of employee IDs who report to this manager
        subordinates = User.query.filter_by(manager_id=session.get('user_id')).all()
        sub_ids = [s.id for s in subordinates]
        expenses = Expense.query.filter(Expense.user_id.in_(sub_ids)).all()
        
    return render_template('all_expenses.html', expenses=expenses)

@app.route('/my_expenses')
def my_expenses():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Corrected to use user_id to match your database model
    expenses = Expense.query.filter_by(user_id=session.get('user_id')).all()
    return render_template('dashboard_employee.html', expenses=expenses)

@app.route('/approval_rules')
def approval_rules():
    # 1. Check if the user is logged in at all
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # 2. Fetch the rules for everyone to see
    company_rules = ApprovalRule.query.filter_by(company_id=session.get('company_id')).all()
    
    # 3. Only Admins need the list of users (for the 'New Rule' form)
    users = []
    if session.get('role') == 'Admin':
        users = User.query.filter_by(company_id=session.get('company_id')).all()
    
    return render_template('approval_rules.html', users=users, rules=company_rules)

@app.route('/create_rule', methods=['POST'])
def create_rule():
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('dashboard'))
        
    name = request.form.get('rule_name')
    target_user_id = request.form.get('target_user_id') or None
    manager_first = request.form.get('manager_first') == '1'
    percentage_threshold = request.form.get('percentage_threshold') or None
    specific_approver_id = request.form.get('specific_approver_id') or None
    is_sequential = request.form.get('is_sequential') == '1'
    approver_ids = request.form.getlist('approver_ids[]')
    
    new_rule = ApprovalRule(
        company_id=session.get('company_id'),
        name=name,
        target_user_id=target_user_id,
        manager_first=manager_first,
        percentage_threshold=percentage_threshold,
        specific_approver_id=specific_approver_id,
        is_sequential=is_sequential
    )
    db.session.add(new_rule)
    db.session.flush() # Get the ID of the new rule before saving the steps
    
    # Save the sequence of approvers
    for index, ap_id in enumerate(approver_ids):
        step = ApprovalRuleStep(
            rule_id=new_rule.id,
            approver_id=ap_id,
            step_order=index + 1
        )
        db.session.add(step)
        
    db.session.commit()
    flash('Approval rule created successfully!', 'success')
    return redirect(url_for('approval_rules'))

@app.route('/delete_rule/<int:rule_id>')
def delete_rule(rule_id):
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('dashboard'))
        
    rule = ApprovalRule.query.get_or_404(rule_id)
    db.session.delete(rule)
    db.session.commit()
    flash('Rule deleted successfully.', 'success')
    return redirect(url_for('approval_rules'))

@app.route('/edit_rule/<int:rule_id>', methods=['POST'])
def edit_rule(rule_id):
    if 'user_id' not in session or session.get('role') != 'Admin':
        return redirect(url_for('dashboard'))
        
    rule = ApprovalRule.query.get_or_404(rule_id)
    
    # 1. Update basic rule details
    rule.name = request.form.get('rule_name')
    target_user_id = request.form.get('target_user_id')
    rule.target_user_id = int(target_user_id) if target_user_id else None
    rule.manager_first = request.form.get('manager_first') == '1'
    
    pct = request.form.get('percentage_threshold')
    rule.percentage_threshold = int(pct) if pct else None
    
    spec_app = request.form.get('specific_approver_id')
    rule.specific_approver_id = int(spec_app) if spec_app else None
    
    rule.is_sequential = request.form.get('is_sequential') == '1'
    
    # 2. Rebuild the approval steps
    approver_ids = request.form.getlist('approver_ids[]')
    
    # Delete the old steps sequence
    ApprovalRuleStep.query.filter_by(rule_id=rule.id).delete()
    
    # Save the new steps sequence
    for index, ap_id in enumerate(approver_ids):
        step = ApprovalRuleStep(
            rule_id=rule.id,
            approver_id=ap_id,
            step_order=index + 1
        )
        db.session.add(step)
        
    db.session.commit()
    flash('Approval rule updated successfully!', 'success')
    return redirect(url_for('approval_rules'))

# (Optional) OCR Endpoint
@app.route('/api/ocr', methods=['POST'])
def api_ocr():
    if 'receipt' not in request.files:
        return jsonify({"error": "No receipt file"}), 400
    try:
        img = Image.open(request.files['receipt'])
        text = pytesseract.image_to_string(img)
        return jsonify({"raw_text": text, "status": "success"})
    except Exception as e:
        return jsonify({"error": str(e), "status": "failed"}), 500

@app.route('/edit_user/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if request.method == 'GET':
        return redirect(url_for('manage_users'))

    if 'user_id' not in session or session.get('role') != 'Admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('dashboard'))

    user_to_edit = User.query.get_or_404(user_id)
    
    # Update fields from form
    user_to_edit.name = request.form.get('name')
    user_to_edit.email = request.form.get('email')
    user_to_edit.role = request.form.get('role') # Updated: captures new role
    
    # Update Manager (New)
    manager_id = request.form.get('manager_id')
    user_to_edit.manager_id = int(manager_id) if manager_id else None
    
    is_approver_str = request.form.get('is_manager_approver', 'false')
    user_to_edit.is_manager_approver = (is_approver_str.lower() == 'true')

    try:
        db.session.commit()
        flash(f'User {user_to_edit.name} updated successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Error updating user.', 'error')

    return redirect(url_for('manage_users'))



# --- 6. Run Application ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)