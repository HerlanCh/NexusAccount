from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from pymongo import MongoClient
from datetime import datetime
import redis
from bson.objectid import ObjectId
import config
from flask import session
from functools import wraps
import json

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user') or not session['user'].get('admin'):
            flash('Acceso no autorizado.')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapped

app = Flask(__name__)
# Configuración de MongoDB
client = MongoClient(config.MONGO_URI, maxPoolSize=100)
db = client[config.MONGO_DB]

# Configuración de Redis
redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    password=config.REDIS_PASSWORD,
    decode_responses=True
)

app.secret_key = 'clave-secreta-super-segura'  # Añade esto si no estaba

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('usuarios'))  # ya logueado

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Buscar en la colección Administradores
        admin = db.Administradores.find_one({'user': username, 'password': password})

        if admin:
            session['user'] = {
                '_id': str(admin['_id']),
                'username': admin['user'],
                'admin': True  # Se asume que todos los de esta colección son administradores
            }
            return redirect(url_for('usuarios'))
        else:
            flash('Usuario o contraseña incorrectos')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Ruta principal - Dashboard
@app.route('/')
def dashboard():
    cache_key = 'dashboard_stats'
    cached_stats = redis_client.get(cache_key)
    
    # Si existe en caché, retornar directamente
    if cached_stats:
        return render_template('dashboard.html', stats=eval(cached_stats))
    
    try:
        # Estadísticas de avatares usando agregación
        avatares_stats = list(db.Avatares.aggregate([
            {
                '$group': {
                    '_id': None,
                    'total_niveles': {'$sum': '$level'},
                    'count': {'$sum': 1}
                }
            }
        ]))
        avatares_data = avatares_stats[0] if avatares_stats else {'total_niveles': 0, 'count': 0}

        # Estadísticas de transacciones usando agregación
        transacciones_stats = list(db.Transacciones.aggregate([
            {
                '$group': {
                    '_id': None,
                    'total_monto': {'$sum': '$amount'},
                    'count': {'$sum': 1}
                }
            }
        ]))
        transacciones_data = transacciones_stats[0] if transacciones_stats else {'total_monto': 0, 'count': 0}

        # Fecha de inicio del día actual en formato ISO
        start_of_day = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat() + 'Z'

        # Últimos usuarios y eventos
        ultimos_usuarios = list(db.Usuarios.find().sort('created_at', -1).limit(5))
        for u in ultimos_usuarios:
            u['_id'] = str(u['_id'])

        ultimos_eventos = list(db.EventosJuego.find().sort('created_at', -1).limit(5))
        for e in ultimos_eventos:
            e['_id'] = str(e['_id'])
            e['user_info'] = db.Usuarios.find_one({'user_id': e['user_id']})
            if e['user_info'] and '_id' in e['user_info']:
                e['user_info']['_id'] = str(e['user_info']['_id'])

        stats = {
            'total_usuarios': db.Usuarios.count_documents({}),
            'total_avatares': avatares_data['count'],
            'total_transacciones': transacciones_data['count'],
            'ultimos_usuarios': ultimos_usuarios,
            'ultimos_eventos': ultimos_eventos,
            'total_niveles_avatares': avatares_data['total_niveles'],
            'total_monto_transacciones': transacciones_data['total_monto'],
            'eventos_hoy': db.EventosJuego.count_documents({
                'created_at': {'$gte': start_of_day}
            }),
            'ultima_actualizacion': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        }

        # Guardar en caché por 5 minutos (300 segundos)
        redis_client.setex(cache_key, 300, json.dumps(stats))

        return render_template('dashboard.html', stats=stats)

    except Exception as e:
        app.logger.error(f"Error generando dashboard: {str(e)}")
        stats = {
            'total_usuarios': db.Usuarios.count_documents({}),
            'total_avatares': db.Avatares.count_documents({}),
            'total_transacciones': db.Transacciones.count_documents({}),
            'ultimos_usuarios': [],
            'ultimos_eventos': [],
            'total_niveles_avatares': 0,
            'total_monto_transacciones': 0,
            'eventos_hoy': 0,
            'error': True
        }
        return render_template('dashboard.html', stats=stats)

# Ruta para Usuarios
@app.route('/usuarios')
@login_required
def usuarios():
    cache_key = 'all_usuarios'
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        usuarios = json.loads(cached_data)
    else:
        usuarios = list(db.Usuarios.find())
        for u in usuarios:
            u['_id'] = str(u['_id'])
        redis_client.setex('all_usuarios', 3600, json.dumps(usuarios))# Cache por 1 hora
    
    # Obtener avatares para los selects
    avatares_list = list(db.Avatares.find())
    return render_template('usuarios.html', usuarios=usuarios, avatares_list=avatares_list)

@app.route('/usuarios/add', methods=['POST'])
def add_usuario():
    data = {
        'user_id': request.form['user_id'],
        'email': request.form['email'],
        'password_hash': request.form['password_hash'],
        'avatar_refs': request.form.getlist('avatar_refs'),
        'created_at': datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'  # Asegurar esto
    }
    db.Usuarios.insert_one(data)
    redis_client.delete('all_usuarios')
    redis_client.delete('dashboard_stats')
    return redirect(url_for('usuarios'))


@app.route('/usuarios/update/<id>', methods=['POST'])
def update_usuario(id):
    try:
        data = {
            'user_id': request.form['user_id'],
            'email': request.form['email'],
            'password_hash': request.form['password_hash'],
            'avatar_refs': request.form.getlist('avatar_refs')
        }
        
        # Actualizar el usuario en MongoDB
        db.Usuarios.update_one(
            {'_id': ObjectId(id)},
            {'$set': data}
        )
        
        # Limpiar caché
        redis_client.delete('all_usuarios')
        
        # Redirigir con mensaje de éxito
        flash('Usuario actualizado correctamente', 'success')
        return redirect(url_for('usuarios'))
        
    except Exception as e:
        # Manejar errores
        flash(f'Error al actualizar usuario: {str(e)}', 'danger')
        return redirect(url_for('usuarios'))

@app.route('/usuarios/delete/<id>')
def delete_usuario(id):
    db.Usuarios.delete_one({'_id': ObjectId(id)})
    redis_client.delete('all_usuarios')
    redis_client.delete('dashboard_stats')
    return redirect(url_for('usuarios'))

# ... (resto de los endpoints CRUD como en el código anterior)

@app.route('/avatares')
@login_required
def avatares():
    cache_key = 'all_avatares'
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        avatares = json.loads(cached_data)
    else:
        avatares = list(db.Avatares.find())
        for avatar in avatares:
            avatar['_id'] = str(avatar['_id'])
        redis_client.setex(cache_key, 3600, json.dumps(avatares))
    
    # Obtener la lista de usuarios para el select
    usuarios_list = list(db.Usuarios.find())
    
    return render_template('avatares.html', avatares=avatares, usuarios_list=usuarios_list)

@app.route('/avatares/add', methods=['POST'])
def add_avatar():
    data = {
        'avatar_id': request.form['avatar_id'],
        'user_id': request.form['user_id'],
        'name': request.form['name'],
        'level': int(request.form['level']),
        'stats': {
            'fuerza': int(request.form['fuerza']),
            'agilidad': int(request.form['agilidad'])
        },
        'created_at': datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'
    }
    db.Avatares.insert_one(data)

    # Añadir el avatar_id al usuario correspondiente
    db.Usuarios.update_one(
        {'user_id': data['user_id']},
        {'$addToSet': {'avatar_refs': data['avatar_id']}}  # Evita duplicados
    )

    # Limpiar caches
    redis_client.delete('all_avatares')
    redis_client.delete('all_usuarios')

    return redirect(url_for('avatares'))


@app.route('/avatares/update/<id>', methods=['POST'])
def update_avatar(id):
    data = {
        'avatar_id': request.form['avatar_id'],
        'user_id': request.form['user_id'],
        'name': request.form['name'],
        'level': int(request.form['level']),
        'stats': {
            'fuerza': int(request.form['fuerza']),
            'agilidad': int(request.form['agilidad'])
        }
    }
    db.Avatares.update_one({'_id': ObjectId(id)}, {'$set': data})
    redis_client.delete('all_avatares')
    return redirect(url_for('avatares'))

@app.route('/avatares/delete/<id>')
def delete_avatar(id):
    try:
        # Verificar que el ID es válido
        from bson.errors import InvalidId
        try:
            avatar_id = ObjectId(id)
        except InvalidId:
            return redirect(url_for('avatares'))
        
        # Buscar el avatar
        avatar = db.Avatares.find_one({'_id': avatar_id})
        
        if not avatar:
            return redirect(url_for('avatares'))
        
        # Eliminar el avatar
        db.Avatares.delete_one({'_id': avatar_id})
        
        # Actualizar referencias en usuarios si existe avatar_id
        if 'avatar_id' in avatar:
            db.Usuarios.update_many(
                {'avatar_refs': avatar['avatar_id']},
                {'$pull': {'avatar_refs': avatar['avatar_id']}}
            )
        
        # Limpiar cachés
        redis_client.delete('all_avatares')
        redis_client.delete('all_usuarios')
        
        # Configurar mensaje flash (requiere secret_key)
        try:
            flash('Avatar eliminado correctamente', 'success')
        except RuntimeError as e:
            app.logger.warning("No se pudo mostrar mensaje flash: %s", str(e))
        
    except Exception as e:
        app.logger.error("Error eliminando avatar: %s", str(e))
        try:
            flash(f'Error al eliminar avatar: {str(e)}', 'error')
        except RuntimeError:
            pass
    
    return redirect(url_for('avatares'))

### Endpoints para Relaciones Sociales ###
@app.route('/relaciones')
def relaciones():
    cache_key = 'all_relaciones'
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        relaciones = json.loads(cached_data)
    else:
        relaciones = list(db.RelacionesSociales.find())
        for r in relaciones:
            r['_id'] = str(r['_id'])
            if r.get('user_1_info') and '_id' in r['user_1_info']:
                r['user_1_info']['_id'] = str(r['user_1_info']['_id'])
            if r.get('user_2_info') and '_id' in r['user_2_info']:
                r['user_2_info']['_id'] = str(r['user_2_info']['_id'])

        redis_client.setex(cache_key, 3600, json.dumps(relaciones))  

    
    usuarios = list(db.Usuarios.find())
    return render_template('relaciones.html', relaciones=relaciones, usuarios=usuarios)

@app.route('/relaciones/add', methods=['POST'])
def add_relacion():
    data = {
        'relation_id': request.form['relation_id'],
        'user_id_1': request.form['user_id_1'],
        'user_id_2': request.form['user_id_2'],
        'type': request.form['type'],
        'created_at': datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'
    }
    db.RelacionesSociales.insert_one(data)
    redis_client.delete('all_relaciones')
    return redirect(url_for('relaciones'))

@app.route('/relaciones/update/<id>', methods=['POST'])
def update_relacion(id):
    data = {
        'relation_id': request.form['relation_id'],
        'user_id_1': request.form['user_id_1'],
        'user_id_2': request.form['user_id_2'],
        'type': request.form['type']
    }
    db.RelacionesSociales.update_one({'_id': ObjectId(id)}, {'$set': data})
    redis_client.delete('all_relaciones')
    return redirect(url_for('relaciones'))

@app.route('/relaciones/delete/<id>')
def delete_relacion(id):
    db.RelacionesSociales.delete_one({'_id': ObjectId(id)})
    redis_client.delete('all_relaciones')
    return redirect(url_for('relaciones'))

### Endpoints para Transacciones ###
@app.route('/transacciones')
@login_required
def transacciones():
    # Caché para transacciones
    cache_key = 'all_transacciones'
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        transacciones = eval(cached_data)
    else:
        transacciones = list(db.Transacciones.find().sort('created_at', -1))
        for t in transacciones:
            t['_id'] = str(t['_id'])
            if t.get('user_info') and '_id' in t['user_info']:
                t['user_info']['_id'] = str(t['user_info']['_id'])

        redis_client.setex(cache_key, 3600, json.dumps(transacciones))



    
    # Caché para estadísticas
    stats_key = 'transacciones_stats'
    cached_stats = redis_client.get(stats_key)
    
    if cached_stats:
        stats = eval(cached_stats)
    else:
        # Cálculos costosos
        stats = {
            'total_ingresos': calcular_total_ingresos(),
            'total_compras': calcular_total_compras(),
            'transacciones_hoy': calcular_transacciones_hoy(),
            'promedio': calcular_promedio_transacciones()
        }
        redis_client.setex(stats_key, 300, str(stats))  # 5 minutos de caché
    
    return render_template(
        'transacciones.html',
        transacciones=transacciones,
        usuarios=list(db.Usuarios.find()),
        **stats
    )

def calcular_total_ingresos():
    try:
        pipeline = [
            {'$match': {'type': {'$in': ['VENTA', 'RECOMPENSA']}}},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ]
        result = list(db.Transacciones.aggregate(pipeline))
        return float(result[0]['total']) if result else 0.0
    except Exception as e:
        print(f"Error calculando ingresos: {str(e)}")
        return 0.0

def calcular_total_compras():
    try:
        pipeline = [
            {'$match': {'type': 'COMPRA'}},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ]
        result = list(db.Transacciones.aggregate(pipeline))
        return float(result[0]['total']) if result else 0.0
    except Exception as e:
        print(f"Error calculando compras: {str(e)}")
        return 0.0

def calcular_transacciones_hoy():
    try:
        start_of_day = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat() + 'Z'
        return db.Transacciones.count_documents({
            'created_at': {'$gte': start_of_day}
        })
    except Exception as e:
        print(f"Error calculando transacciones hoy: {str(e)}")
        return 0

def calcular_promedio_transacciones():
    try:
        total = calcular_total_ingresos()
        count = db.Transacciones.count_documents({})
        return round(total / count, 2) if count > 0 else 0.0
    except Exception as e:
        print(f"Error calculando promedio: {str(e)}")
        return 0.0

@app.route('/transacciones/add', methods=['POST'])
def add_transaccion():
    try:
        # Validación de datos
        required_fields = ['transaction_id', 'user_id', 'type', 'amount']
        if not all(field in request.form for field in required_fields):
            return redirect(url_for('transacciones', error='Faltan campos requeridos'))

        # Verificar duplicados
        if db.Transacciones.find_one({'transaction_id': request.form['transaction_id']}):
            return redirect(url_for('transacciones', error='ID de transacción ya existe'))

        # Crear la transacción
        new_transaction = {
            'transaction_id': request.form['transaction_id'],
            'user_id': request.form['user_id'],
            'type': request.form['type'],
            'amount': float(request.form['amount']),
            'created_at': datetime.utcnow().isoformat() + 'Z'
        }

        db.Transacciones.insert_one(new_transaction)
        redis_client.delete('all_transacciones')  # Limpiar caché
        
        # Redirigir con mensaje de éxito
        return redirect(url_for('transacciones', success='Transacción agregada correctamente'))

    except Exception as e:
        flash(f'Error: {str(e)}', 'danger')
    return redirect(url_for('transacciones'))
    


@app.route('/transacciones/update/<id>', methods=['POST'])
def update_transaccion(id):
    try:
        data = {
            'transaction_id': request.form['transaction_id'],
            'user_id': request.form['user_id'],
            'type': request.form['type'],
            'amount': float(request.form['amount'])
        }
        db.Transacciones.update_one({'_id': ObjectId(id)}, {'$set': data})
        # Limpiar cachés relevantes
        redis_client.delete('all_transacciones')
        redis_client.delete('transacciones_stats')
        return redirect(url_for('transacciones'))
    except Exception as e:
        print(f"Error actualizando transacción: {str(e)}")
        flash('Error al actualizar la transacción', 'error')
        return redirect(url_for('transacciones'))

@app.route('/transacciones/delete/<id>')
def delete_transaccion(id):
    try:
        db.Transacciones.delete_one({'_id': ObjectId(id)})
        # Limpiar cachés relevantes
        redis_client.delete('all_transacciones')
        redis_client.delete('transacciones_stats')
        return redirect(url_for('transacciones'))
    except Exception as e:
        print(f"Error eliminando transacción: {str(e)}")
        flash('Error al eliminar la transacción', 'error')
        return redirect(url_for('transacciones'))

### Endpoints para Eventos de Juego ###
@app.route('/eventos')
def eventos():
    cache_key = 'all_eventos'
    cached_data = redis_client.get(cache_key)
    
    if cached_data:
        eventos = eval(cached_data)
    else:
        eventos = list(db.EventosJuego.find().sort('created_at', -1))
        for e in eventos:
            e['_id'] = str(e['_id'])
            if e.get('user_info') and '_id' in e['user_info']:
                e['user_info']['_id'] = str(e['user_info']['_id'])

        redis_client.setex(cache_key, 3600, json.dumps(eventos))

    
    usuarios = list(db.Usuarios.find())
    return render_template('eventos.html', eventos=eventos, usuarios=usuarios)

@app.route('/eventos/add', methods=['POST'])
def add_evento():
    data = {
        'event_id': request.form['event_id'],
        'user_id': request.form['user_id'],
        'event_name': request.form['event_name'],
        'event_details': {
            'nivel_alcanzado': int(request.form['nivel_alcanzado']),
            'premio': request.form['premio']
        },
        'created_at': datetime.utcnow().isoformat(timespec='milliseconds') + 'Z'
    }
    db.EventosJuego.insert_one(data)
    redis_client.delete('all_eventos')
    return redirect(url_for('eventos'))

@app.route('/eventos/update/<id>', methods=['POST'])
def update_evento(id):
    data = {
        'event_id': request.form['event_id'],
        'user_id': request.form['user_id'],
        'event_name': request.form['event_name'],
        'event_details': {
            'nivel_alcanzado': int(request.form['nivel_alcanzado']),
            'premio': request.form['premio']
        }
    }
    db.EventosJuego.update_one({'_id': ObjectId(id)}, {'$set': data})
    redis_client.delete('all_eventos')
    return redirect(url_for('eventos'))

@app.route('/eventos/delete/<id>')
def delete_evento(id):
    db.EventosJuego.delete_one({'_id': ObjectId(id)})
    redis_client.delete('all_eventos')
    return redirect(url_for('eventos'))

# Crear admin si no existe (inmediato al arrancar)
if db.Usuarios.count_documents({'username': 'admin'}) == 0:
    db.Usuarios.insert_one({
        'username': 'admin',
        'password': 'admin123',
        'admin': True
    })
    print("✅ Usuario admin creado (admin / admin123)")
else:
    print("🔒 Usuario admin ya existe")

if __name__ == '__main__':
    app.run(debug=True)