# Application modules
from flask import Flask, jsonify, request
from flaskext.mysql import MySQL
from marshmallow import ValidationError
from flask_bcrypt import Bcrypt
import jwt

# General modules
from functools import wraps
from datetime import datetime, timedelta, date

# Custom modules
from db import MySQLConnection
from random_password_generator import random_password_generator
from schema import AddUser, UserLogin
from util import get_items, generate_placeholders

# creating an instance of Flask class
app = Flask(__name__)

app.config.from_pyfile('config.py')
mysql = MySQL()
mysql.init_app(app)
bcrypt = Bcrypt(app)

# For random password generation on account creation
generate_password = random_password_generator()


def auth_wrapper(method):
	@wraps(method)
	def wrapper(*args, **kwargs):
		access_token = request.headers.get('jwt-auth-token')
		try:
			payload = jwt.decode(access_token.encode('utf-8'), app.config['TOKEN_KEY'], algorithms='HS256')
			username = payload['username']
			current_user = None
			user_type = None

			with MySQLConnection(mysql) as connection:
				with connection.cursor() as cur:
					get_user = "SELECT username, id, user_type FROM EmployeeLogin WHERE username = %s"
					cur.execute(get_user, username)
					fetched_username, current_user, user_type = cur.fetchall()[0]

					if not fetched_username:
						raise Exception('Invalid Token')					

			return method(current_user_id=current_user, current_user_type=user_type, *args, **kwargs)
			
		except jwt.exceptions.ExpiredSignatureError as e:
			return jsonify({ 'message': 'Session Expired' }), 403

		except Exception as e:
			print('Error:', e)
			return jsonify({ 'message': 'Forbidden' }), 403

	return wrapper


@app.route('/api/login', methods=['POST'])
def login():
	try:
		data = UserLogin().load(request.get_json(force = True))
		username, password = data['username'], data['password']

		with MySQLConnection(mysql) as connection:
			with connection.cursor() as cur:
				get_password = "SELECT password FROM EmployeeLogin WHERE username = %s"
				cur.execute(get_password, username)
				password_hash = cur.fetchall()[0][0]

				if not password_hash or not bcrypt.check_password_hash(password_hash, password):
					raise Exception('Invalid Credentials')
					pass

		payload = { 'username': username, 'exp': datetime.utcnow() + timedelta(minutes=5) }
		token = jwt.encode(payload, app.config['TOKEN_KEY'], algorithm='HS256')
		# print(token, token.decode('utf-8').encode('utf-8'))
		return jsonify({ 'access_token': token.decode('utf-8') })

	except Exception as e:
		print('Error:', e)
		return jsonify({ 'message': 'Authentication Error' }), 401


@app.route('/api/users', methods=['GET'])
def get_users():
	try:
		with MySQLConnection(mysql) as connection:
			with connection.cursor() as cur:
				fetch_users = "SELECT d.first_name, d.last_name, d.title, l.user_type FROM EmployeeDetails AS d INNER JOIN EmployeeLogin AS l ON d.id=l.id"
				cur.execute(fetch_users)
				result = cur.fetchall()

				users = []
				for user in result:
					fname, lname, title, utype = user
					users.append({ 'first_name': fname, 'last_name': lname, 'title': title, 'user_type': utype })
		
		return jsonify({ 'data': users })
	
	except Exception as e:
		print('Error: ', e)
		return jsonify({ 'message': 'Unexpected Error Occured' }), 500


@app.route('/api/user/<user_id>', methods=['GET'])
@auth_wrapper
def get_user(current_user_id, user_id, **kwargs):
	try:
		data = {}
		if int(user_id) == current_user_id:
			with MySQLConnection(mysql) as connection:
				with connection.cursor() as cur:
					fetch_users = "SELECT d.first_name, d.last_name, d.title, d.phone_number, d.date_of_birth, d.email, d.creation_time, d.modification_time, l.username, l.user_type FROM (SELECT * FROM EmployeeDetails WHERE id = %s) AS d INNER JOIN EmployeeLogin AS l ON d.id=l.id"
					cur.execute(fetch_users, user_id)
					
					user = cur.fetchall()[0]
					data = {
						'first_name': user[0],
						'last_name': user[1],
						'title': user[2],
						'phone_number': user[3],
						'date_of_birth': user[4],
						'email': user[5],
						'creation_time': user[6],
						'modification_time': user[7],
						'username': user[8],
						'user_type': user[9],
					}

		else:
			with MySQLConnection(mysql) as connection:
				with connection.cursor() as cur:
					fetch_users = "SELECT d.first_name, d.last_name, d.title, l.user_type FROM (SELECT * FROM EmployeeDetails WHERE id = %s) AS d INNER JOIN EmployeeLogin AS l ON d.id=l.id"
					cur.execute(fetch_users, user_id)
					
					user = cur.fetchall()[0]
					data = {
						'first_name': user[0],
						'last_name': user[1],
						'title': user[2],
						'user_type': user[3],
					}
		
		return jsonify({ 'data': data })
	
	except Exception as e:
		print('Error: ', e)
		return jsonify({ 'message': 'Unexpected Error Occured' }), 500


@app.route('/api/add', methods=['POST'])
@auth_wrapper
def add_user(current_user_id, current_user_type):
	connection = None
	try:
		data = AddUser().load(request.get_json(force = True))

		username, email, user_type = data['username'], data['email'], data['user_type']
		del data['username']
		del data['user_type']

		if data.get('options', None):
			options = data['options']
			del data['options']
		else:
			options = {}

		keys, values = get_items(data)
		connection = mysql.connect()

		with connection.cursor() as cur:
			if options.get('is_primary', None):
				# dont insert another primary user if one is already present
				primary_users = "SELECT count(id) FROM AdminInfo where is_primary=true"
				cur.execute(primary_users)
				primary_user_count = cur.fetchall()[0][0]
				if primary_user_count:
					raise ValueError('Primary user already exists')

			detail_query = "INSERT INTO EmployeeDetails ("+keys+") VALUES ("+generate_placeholders(len(values))+")"
			cur.execute(detail_query, values)

			cur.execute("SELECT id FROM EmployeeDetails WHERE email=%s", (email))
			user_id = cur.fetchall()[0][0]
			password = generate_password()
			pw_hash = bcrypt.generate_password_hash(password)
			login_query = "INSERT INTO EmployeeLogin VALUES ("+generate_placeholders(4)+")"
			cur.execute(login_query, (user_id, username, pw_hash, user_type))

			if user_type == 'admin':
				if current_user_type != 'admin':
					raise ValueError('Invalied operation')
				options['id'] = user_id
				keys, values = get_items(options)

				role_query = "INSERT INTO AdminInfo ("+keys+") VALUES ("+generate_placeholders(len(values))+")"
				cur.execute(role_query, values)	

			elif user_type == 'manager':
				if current_user_type != 'admin':
					raise ValueError('Invalied operation')
				options['id'] = user_id
				options['added_by'] = current_user_id
				keys, values = get_items(options)

				role_query = "INSERT INTO ManagerInfo ("+keys+") VALUES ("+generate_placeholders(len(values))+")"
				cur.execute(role_query, values)	

			elif user_type == 'staff':
				if current_user_type not in ('admin', 'manager'):
					raise ValueError('Invalied operation')
				options['id'] = user_id
				options['added_by'] = current_user_id
				keys, values = get_items(options)

				role_query = "INSERT INTO StaffInfo ("+keys+") VALUES ("+generate_placeholders(len(values))+")"
				cur.execute(role_query, values)	

		connection.commit()
		return jsonify({ 'message': 'User Created' }), 201

	except ValidationError as err:
		return jsonify({ 'message': err.messages }), 400

	except ValueError as err:
		return jsonify({ 'message': err }), 400

	except Exception as err:
		if connection:
			connection.rollback()
		print("Unexpected error:", err)
		return jsonify({ 'message': 'Cannot process request' }), 500
		
	finally:
		if connection:
			connection.close()


@app.route('/', methods=['GET'])
def home():
	return 'Hello There!'

if __name__ == '__main__':
	app.run()
