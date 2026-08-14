#!/usr/bin/env python3
"""
ThreadChess - Servidor Flask + SocketIO
Integra HTTP (para servir HTML) + WebSocket (para juego) en un solo puerto
Versión para Render
"""

import os
import logging
from flask import Flask
from flask_socketio import SocketIO, emit, request
from threadchess_server import ChessGame, ChessRoom
import random
import string
from threading import RLock

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ThreadChess-SocketIO')

# Crear app Flask
app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'threadchess-secret-key'

# Configurar SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Variables globales
rooms = {}  # {room_id: ChessRoom}
user_room = {}  # {sid: room_id}
rooms_lock = RLock()

def generate_room_id():
    """Genera un ID único para la sala"""
    while True:
        room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        with rooms_lock:
            if room_id not in rooms:
                return room_id


@app.route('/')
def index():
    """Sirve el archivo principal HTML"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except:
        return 'index.html no encontrado', 404


@socketio.on('connect')
def handle_connect():
    """Maneja conexión de cliente"""
    sid = request.sid
    logger.info(f"Cliente conectado: {sid}")
    emit('connected', {'message': 'Conectado al servidor ThreadChess'})


@socketio.on('disconnect')
def handle_disconnect():
    """Maneja desconexión de cliente"""
    sid = request.sid
    
    with rooms_lock:
        if sid in user_room:
            room_id = user_room[sid]
            del user_room[sid]
            
            if room_id in rooms:
                rooms[room_id].remove_player(sid)
                if rooms[room_id].get_player_count() == 0:
                    del rooms[room_id]
                    logger.info(f"Sala {room_id} eliminada")
    
    logger.info(f"Cliente desconectado: {sid}")


@socketio.on('connect_player')
def handle_player_connect(data):
    """Maneja conexión del jugador"""
    player_name = data.get('player_name', f'Player{random.randint(1000, 9999)}')
    sid = request.sid
    logger.info(f"Jugador conectado: {player_name} ({sid})")
    
    emit('player_connected', {'player_name': player_name})


@socketio.on('create_room')
def handle_create_room():
    """Crea una nueva sala"""
    sid = request.sid
    room_id = generate_room_id()
    
    with rooms_lock:
        rooms[room_id] = ChessRoom(room_id)
        user_room[sid] = room_id
        rooms[room_id].add_player(sid, 'Player1')
    
    logger.info(f"Sala creada: {room_id}")
    emit('room_created', {'room_id': room_id, 'message': f'Sala creada. Comparte este código: {room_id}'})


@socketio.on('join_room')
def handle_join_room(data):
    """Unirse a una sala"""
    sid = request.sid
    room_id = data.get('room_id')
    player_name = data.get('player_name', f'Player{random.randint(1000, 9999)}')
    
    with rooms_lock:
        if room_id not in rooms:
            emit('error', {'message': 'Sala no encontrada'})
            return
        
        room = rooms[room_id]
        if not room.add_player(sid, player_name):
            emit('error', {'message': 'Sala llena'})
            return
    
    with rooms_lock:
        user_room[sid] = room_id
    
    room = rooms[room_id]
    
    # Enviar confirmación al que se une
    emit('room_joined', {'room_id': room_id, 'player_count': room.get_player_count()})
    
    # Si sala está completa, iniciar juego
    if room.get_player_count() == 2:
        # Asignar colores aleatoriamente
        players = list(room.players.keys())
        
        # Crear partida
        player1_name = room.players[players[0]]
        player2_name = room.players[players[1]]
        room.game = ChessGame(room_id, player1_name, player2_name)
        
        game_state = room.game.get_state()
        
        # Enviar estado a ambos
        socketio.emit('game_started', {'state': game_state}, room=room_id)
        
        logger.info(f"Partida iniciada en sala {room_id}")


@socketio.on('move')
def handle_move(data):
    """Maneja un movimiento de pieza"""
    sid = request.sid
    move_uci = data.get('move')
    
    with rooms_lock:
        room_id = user_room.get(sid)
    
    if not room_id or room_id not in rooms:
        emit('error', {'message': 'Sala no encontrada'})
        return
    
    room = rooms[room_id]
    if not room.game:
        emit('error', {'message': 'Partida no iniciada'})
        return
    
    game = room.game
    
    # Hacer movimiento
    success, result = game.make_move(move_uci)
    
    if not success:
        emit('error', {'message': result})
        return
    
    # Enviar estado actualizado a ambos jugadores
    game_state = game.get_state()
    socketio.emit('move_made', {'state': game_state, 'move': move_uci}, room=room_id)
    
    logger.info(f"Movimiento en {room_id}: {move_uci}")


@socketio.on('get_legal_moves')
def handle_get_legal_moves(data):
    """Retorna movimientos legales desde una casilla"""
    sid = request.sid
    square = data.get('square')
    
    with rooms_lock:
        room_id = user_room.get(sid)
    
    if room_id and room_id in rooms:
        room = rooms[room_id]
        if room.game:
            legal_moves = room.game.get_legal_moves(square)
            emit('legal_moves', {'square': square, 'moves': legal_moves})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"ThreadChess iniciando en puerto {port}")
    logger.info("Autores: Andrés Felipe Gómez Gutiérrez, Brayan David Roa Vega, Sebastián David Tojuelo Perilla")
    
    # En producción (Render), usar el servidor SocketIO
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
