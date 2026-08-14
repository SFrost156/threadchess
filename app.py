#!/usr/bin/env python3
"""
ThreadChess - Servidor Flask + SocketIO
Versión simplificada y funcional para Render
"""

import os
import logging
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import random
import string
from threading import RLock
import chess

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ThreadChess')

# Crear app Flask
app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'threadchess-secret-key-2026'

# Configurar SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

# Variables globales
games = {}  # {room_id: {board, players, move_history}}
players = {}  # {sid: {name, room_id, color}}
games_lock = RLock()

def generate_room_id():
    """Genera un ID único para la sala"""
    while True:
        room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if room_id not in games:
            return room_id


@app.route('/')
def index():
    """Sirve el archivo principal HTML"""
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return 'index.html no encontrado', 404


@socketio.on('connect')
def handle_connect():
    """Maneja conexión de cliente"""
    sid = request.sid
    logger.info(f"Cliente conectado: {sid}")


@socketio.on('disconnect')
def handle_disconnect():
    """Maneja desconexión de cliente"""
    sid = request.sid
    
    with games_lock:
        if sid in players:
            room_id = players[sid].get('room_id')
            del players[sid]
            
            if room_id and room_id in games:
                # Notificar al otro jugador
                socketio.emit('player_left', {'message': 'El otro jugador se desconectó'}, room=room_id)
                del games[room_id]
                logger.info(f"Sala {room_id} eliminada por desconexión")
    
    logger.info(f"Cliente desconectado: {sid}")


@socketio.on('create_room')
def handle_create_room(data):
    """Crea una nueva sala"""
    sid = request.sid
    player_name = data.get('player_name', 'Jugador')
    
    room_id = generate_room_id()
    
    with games_lock:
        games[room_id] = {
            'board': chess.Board(),
            'players': {sid: player_name},
            'move_history': [],
            'colors': {sid: 'white'}  # Primer jugador es blanco
        }
        players[sid] = {'name': player_name, 'room_id': room_id, 'color': 'white'}
    
    join_room(room_id)
    logger.info(f"Sala creada: {room_id} por {player_name}")
    
    emit('room_created', {
        'room_id': room_id,
        'player_name': player_name,
        'message': f'Sala creada. Comparte este código: {room_id}'
    })


@socketio.on('join_room')
def handle_join_room(data):
    """Unirse a una sala"""
    sid = request.sid
    room_id = data.get('room_id', '').upper()
    player_name = data.get('player_name', 'Jugador')
    
    with games_lock:
        if room_id not in games:
            emit('error', {'message': 'Sala no encontrada'})
            return
        
        game = games[room_id]
        
        if len(game['players']) >= 2:
            emit('error', {'message': 'Sala llena (máximo 2 jugadores)'})
            return
        
        # Agregar jugador
        game['players'][sid] = player_name
        game['colors'][sid] = 'black'  # Segundo jugador es negro
        players[sid] = {'name': player_name, 'room_id': room_id, 'color': 'black'}
    
    join_room(room_id)
    logger.info(f"Jugador {player_name} se unió a sala {room_id}")
    
    # Notificar a ambos que la partida inicia
    game = games[room_id]
    game_state = {
        'fen': str(game['board'].fen()),
        'white_player': game['players'][list(game['players'].keys())[0]],
        'black_player': game['players'][list(game['players'].keys())[1]] if len(game['players']) > 1 else '',
        'current_turn': 'white',
        'move_history': game['move_history'],
        'game_over': False,
        'result': None
    }
    
    socketio.emit('game_started', {'state': game_state}, room=room_id)


@socketio.on('move')
def handle_move(data):
    """Maneja un movimiento de pieza"""
    sid = request.sid
    move_uci = data.get('move', '')
    
    with games_lock:
        if sid not in players:
            emit('error', {'message': 'No estás en una sala'})
            return
        
        room_id = players[sid]['room_id']
        
        if room_id not in games:
            emit('error', {'message': 'Sala no encontrada'})
            return
        
        game = games[room_id]
        board = game['board']
        
        # Validar y hacer movimiento
        try:
            move = chess.Move.from_uci(move_uci)
            
            if move not in board.legal_moves:
                emit('error', {'message': 'Movimiento inválido'})
                return
            
            board.push(move)
            game['move_history'].append(move_uci)
            
            # Preparar estado actualizado
            game_state = {
                'fen': str(board.fen()),
                'move_history': game['move_history'],
                'current_turn': 'black' if board.turn else 'white',
                'game_over': board.is_game_over(),
                'result': 'checkmate' if board.is_checkmate() else 'stalemate' if board.is_stalemate() else 'draw' if board.is_insufficient_material() else None
            }
            
            # Enviar a ambos jugadores
            socketio.emit('move_made', {
                'state': game_state,
                'move': move_uci
            }, room=room_id)
            
            logger.info(f"Movimiento en {room_id}: {move_uci}")
            
        except Exception as e:
            logger.error(f"Error en movimiento: {e}")
            emit('error', {'message': f'Error: {str(e)}'})


@socketio.on('get_legal_moves')
def handle_get_legal_moves(data):
    """Retorna movimientos legales desde una casilla"""
    sid = request.sid
    square_index = data.get('square', -1)
    
    with games_lock:
        if sid not in players:
            return
        
        room_id = players[sid]['room_id']
        
        if room_id not in games:
            return
        
        game = games[room_id]
        board = game['board']
        
        # Convertir índice a notación chess
        try:
            row = 7 - (square_index // 8)
            col = square_index % 8
            square = chr(97 + col) + str(row + 1)
            square_obj = chess.parse_square(square)
            
            # Obtener movimientos legales desde esta casilla
            legal_moves = [move.uci() for move in board.legal_moves if move.from_square == square_obj]
            
            emit('legal_moves', {
                'square': square_index,
                'moves': legal_moves
            })
        except Exception as e:
            logger.error(f"Error obteniendo movimientos legales: {e}")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"ThreadChess iniciando en puerto {port}")
    logger.info("Autores: Andrés Felipe Gómez Gutiérrez, Brayan David Roa Vega, Sebastián David Tojuelo Perilla")
    
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
