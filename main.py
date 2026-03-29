
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.secret_key = 'replace_this_with_a_super_secret_key_for_production' # Needed for sessions
# Replace with your actual PostgreSQL credentials
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://username:password@localhost:5432/reimbursement_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5000)