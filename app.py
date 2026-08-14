#!/usr/bin/env python3
"""
ThreadChess - Servidor Flask + SocketIO
Con validación correcta de turnos, coronación, enroque y detección de jaque
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
games = {}  # {room_id: {board, players, move_history, player_colors}}
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
            'player_colors': {sid: 'white'},
            'move_history': []
        }
        players[sid] = {'name': player_name, 'room_id': room_id, 'color': 'white'}
    
    join_room(room_id)
    logger.info(f"Sala creada: {room_id} por {player_name} (white)")
    
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
        
        # Agregar jugador como negro
        game['players'][sid] = player_name
        game['player_colors'][sid] = 'black'
        players[sid] = {'name': player_name, 'room_id': room_id, 'color': 'black'}
    
    join_room(room_id)
    logger.info(f"Sala {room_id}: {player_name} se unió como black")
    
    # Obtener lista ordenada de jugadores
    game = games[room_id]
    player_sids = list(game['players'].keys())
    
    white_player = game['players'][player_sids[0]]
    black_player = game['players'][player_sids[1]] if len(player_sids) > 1 else ''
    
    board = game['board']
    
    game_state = {
        'fen': str(board.fen()),
        'white_player': white_player,
        'black_player': black_player,
        'current_turn': 'white',
        'move_history': game['move_history'],
        'game_over': False,
        'result': None,
        'in_check': board.is_check(),
        'check_color': 'white' if board.is_check() and board.turn else ('black' if board.is_check() else None)
    }
    
    socketio.emit('game_started', {'state': game_state}, room=room_id)


@socketio.on('move')
def handle_move(data):
    """Maneja un movimiento de pieza - CON VALIDACIÓN Y DETECCIÓN DE JAQUE"""
    sid = request.sid
    move_uci = data.get('move', '')
    
    with games_lock:
        if sid not in players:
            emit('error', {'message': 'No estás en una sala'})
            logger.warning(f"Movimiento de jugador desconocido: {sid}")
            return
        
        room_id = players[sid]['room_id']
        player_color = players[sid]['color']
        
        if room_id not in games:
            emit('error', {'message': 'Sala no encontrada'})
            logger.warning(f"Movimiento en sala inexistente: {room_id}")
            return
        
        game = games[room_id]
        board = game['board']
        
        # Validación: ¿Es el turno de este jugador?
        current_turn = 'white' if board.turn else 'black'
        if current_turn != player_color:
            emit('error', {'message': f'No es tu turno. Turno actual: {current_turn}'})
            logger.warning(f"Intento de mover en turno ajeno: {player_color} intentó mover cuando es turno de {current_turn}")
            return
        
        logger.info(f"Movimiento válido: {player_color} ({players[sid]['name']}) intenta {move_uci}")
        
        try:
            # Parsear movimiento
            move = chess.Move.from_uci(move_uci)
            
            # Validación: ¿Es un movimiento legal?
            if move not in board.legal_moves:
                logger.warning(f"Movimiento ilegal: {move_uci}")
                emit('error', {'message': 'Movimiento inválido'})
                return
            
            # Validación: ¿La pieza a mover es del color correcto?
            piece = board.piece_at(move.from_square)
            if piece is None:
                emit('error', {'message': 'No hay pieza en esa casilla'})
                logger.warning(f"Intento de mover casilla vacía: {move_uci}")
                return
            
            piece_color = 'white' if piece.color else 'black'
            if piece_color != player_color:
                emit('error', {'message': 'No puedes mover fichas del oponente'})
                logger.warning(f"INTENTO DE FRAUDE: {player_color} intentó mover pieza de {piece_color}")
                return
            
            # ✅ TODAS LAS VALIDACIONES PASARON - EJECUTAR MOVIMIENTO
            board.push(move)
            game['move_history'].append(move_uci)
            
            # Calcular próximo turno
            current_turn = 'white' if board.turn else 'black'
            
            # Obtener nombres de jugadores
            player_sids = list(game['players'].keys())
            white_player = game['players'][player_sids[0]]
            black_player = game['players'][player_sids[1]] if len(player_sids) > 1 else ''
            
            # Detectar jaque
            in_check = board.is_check()
            check_color = current_turn if in_check else None
            
            # Preparar estado actualizado
            game_state = {
                'fen': str(board.fen()),
                'white_player': white_player,
                'black_player': black_player,
                'move_history': game['move_history'],
                'current_turn': current_turn,
                'game_over': board.is_game_over(),
                'result': 'checkmate' if board.is_checkmate() else 'stalemate' if board.is_stalemate() else 'insufficient_material' if board.is_insufficient_material() else None,
                'in_check': in_check,
                'check_color': check_color
            }
            
            # Enviar a ambos jugadores
            socketio.emit('move_made', {
                'state': game_state,
                'move': move_uci
            }, room=room_id)
            
            logger.info(f"✅ Movimiento ejecutado: {player_color} movió {move_uci} - Próximo turno: {current_turn}")
            
        except ValueError as e:
            logger.error(f"Error en movimiento {move_uci}: {e}")
            emit('error', {'message': f'Movimiento inválido: {move_uci}'})
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
            logger.warning(f"Solicitud de movimientos legales de jugador desconocido: {sid}")
            return
        
        room_id = players[sid]['room_id']
        player_color = players[sid]['color']
        
        if room_id not in games:
            logger.warning(f"Solicitud de movimientos legales en sala inexistente: {room_id}")
            return
        
        game = games[room_id]
        board = game['board']
        
        try:
            row = 7 - (square_index // 8)
            col = square_index % 8
            square = chr(97 + col) + str(row + 1)
            square_obj = chess.parse_square(square)
            
            piece = board.piece_at(square_obj)
            
            if piece is None:
                emit('legal_moves', {
                    'square': square_index,
                    'moves': []
                })
                return
            
            piece_color = 'white' if piece.color else 'black'
            if piece_color != player_color:
                logger.warning(f"Intento de obtener movimientos de ficha ajena: {player_color} intentó seleccionar ficha de {piece_color}")
                emit('legal_moves', {
                    'square': square_index,
                    'moves': []
                })
                return
            
            legal_moves = []
            for move in board.legal_moves:
                if move.from_square == square_obj:
                    legal_moves.append(move.uci())
            
            emit('legal_moves', {
                'square': square_index,
                'moves': legal_moves
            })
            
            logger.debug(f"{player_color} solicitó movimientos desde {square}: {len(legal_moves)} movimientos legales")
            
        except Exception as e:
            logger.error(f"Error obteniendo movimientos legales: {e}")
            emit('legal_moves', {
                'square': square_index,
                'moves': []
            })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"ThreadChess iniciando en puerto {port}")
    logger.info("Autores: Andrés Felipe Gómez Gutiérrez, Brayan David Roa Vega, Sebastián David Tojuelo Perilla")
    
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)
