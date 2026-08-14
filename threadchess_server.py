#!/usr/bin/env python3
"""
ThreadChess - Juego de Ajedrez Online Multiplayer
Autores: Andrés Felipe Gómez Gutiérrez, Brayan David Roa Vega, Sebastián David Tojuelo Perilla
Servidor WebSocket con threading y concurrencia
"""

import asyncio
import websockets
import json
import logging
import random
import string
from threading import Lock, RLock, Thread
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import chess
import signal
import sys

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('threadchess.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ThreadChess')

# ==================== CLASES DEL JUEGO ====================

class ChessGame:
    """Representa una partida de ajedrez con lógica completa"""
    
    def __init__(self, room_id, player1_name, player2_name):
        self.room_id = room_id
        self.board = chess.Board()
        self.players = {}
        
        # Asignar colores aleatoriamente
        colors = ['white', 'black']
        random.shuffle(colors)
        
        self.players['white'] = {'name': player1_name, 'websocket': None}
        self.players['black'] = {'name': player2_name, 'websocket': None}
        
        # Si el segundo índice es black, intercambiar
        if colors[0] == 'black':
            self.players['white'], self.players['black'] = self.players['black'], self.players['white']
        
        self.current_turn = 'white'
        self.move_history = []
        self.start_time = datetime.now()
        self.game_over = False
        self.result = None
        self.move_lock = RLock()
        
    def make_move(self, move_uci):
        """Ejecuta un movimiento en la partida"""
        with self.move_lock:
            try:
                move = chess.Move.from_uci(move_uci)
                if move not in self.board.legal_moves:
                    return False, "Movimiento ilegal"
                
                self.board.push(move)
                self.move_history.append(move_uci)
                
                # Cambiar turno
                self.current_turn = 'black' if self.current_turn == 'white' else 'white'
                
                # Verificar estado del juego
                if self.board.is_checkmate():
                    self.game_over = True
                    winner = 'black' if self.current_turn == 'white' else 'white'
                    self.result = f"{winner}_checkmate"
                    return True, "checkmate"
                elif self.board.is_stalemate():
                    self.game_over = True
                    self.result = "stalemate"
                    return True, "stalemate"
                elif self.board.is_insufficient_material():
                    self.game_over = True
                    self.result = "insufficient_material"
                    return True, "draw"
                elif self.board.can_claim_draw():
                    return True, "draw_available"
                
                return True, "ok"
            except Exception as e:
                logger.error(f"Error en movimiento: {e}")
                return False, str(e)
    
    def get_legal_moves(self, square_index):
        """Obtiene movimientos legales desde una casilla"""
        legal_moves = []
        for move in self.board.legal_moves:
            if move.from_square == square_index:
                legal_moves.append(move.uci())
        return legal_moves
    
    def get_state(self):
        """Retorna el estado actual del juego"""
        return {
            'fen': self.board.fen(),
            'current_turn': self.current_turn,
            'white_player': self.players['white']['name'],
            'black_player': self.players['black']['name'],
            'move_history': self.move_history,
            'game_over': self.game_over,
            'result': self.result,
            'is_check': self.board.is_check(),
            'is_checkmate': self.board.is_checkmate(),
            'is_stalemate': self.board.is_stalemate(),
            'legal_moves': [move.uci() for move in self.board.legal_moves]
        }


class ChessRoom:
    """Representa una sala de juego"""
    
    def __init__(self, room_id):
        self.room_id = room_id
        self.players = {}  # {websocket: player_name}
        self.game = None
        self.players_lock = RLock()
        self.created_at = datetime.now()
        
    def add_player(self, websocket, player_name):
        """Añade un jugador a la sala"""
        with self.players_lock:
            if len(self.players) >= 2:
                return False
            self.players[websocket] = player_name
            logger.info(f"Jugador {player_name} se unió a sala {self.room_id}")
            return True
    
    def remove_player(self, websocket):
        """Remueve un jugador de la sala"""
        with self.players_lock:
            if websocket in self.players:
                del self.players[websocket]
                logger.info(f"Jugador desconectado de sala {self.room_id}")
                return True
            return False
    
    def is_full(self):
        """Verifica si la sala está llena"""
        with self.players_lock:
            return len(self.players) == 2
    
    def get_player_count(self):
        """Obtiene cantidad de jugadores"""
        with self.players_lock:
            return len(self.players)
    
    def start_game(self):
        """Inicia una partida cuando hay 2 jugadores"""
        with self.players_lock:
            if len(self.players) == 2:
                websockets_list = list(self.players.keys())
                names = [self.players[ws] for ws in websockets_list]
                self.game = ChessGame(self.room_id, names[0], names[1])
                self.game.players['white']['websocket'] = websockets_list[0]
                self.game.players['black']['websocket'] = websockets_list[1]
                logger.info(f"Partida iniciada en sala {self.room_id}")
                return True
            return False


class ChessServer:
    """Servidor central de ThreadChess"""
    
    def __init__(self, host='0.0.0.0', port=8765):
        self.host = host
        self.port = port
        self.rooms = {}  # {room_id: ChessRoom}
        self.rooms_lock = RLock()
        self.user_room = {}  # {websocket: room_id}
        self.user_lock = RLock()
        self.executor = ThreadPoolExecutor(max_workers=8)
        logger.info(f"ThreadChess Server inicializado en {host}:{port}")
    
    def generate_room_id(self):
        """Genera un ID único para la sala"""
        while True:
            room_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            with self.rooms_lock:
                if room_id not in self.rooms:
                    return room_id
    
    async def handle_client(self, websocket, path):
        """Maneja conexión de un cliente"""
        client_id = id(websocket)
        player_name = None
        room_id = None
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    action = data.get('action')
                    
                    # Acción: Conectar
                    if action == 'connect':
                        player_name = data.get('player_name', f'Player{random.randint(1000, 9999)}')
                        await websocket.send(json.dumps({
                            'type': 'connected',
                            'player_name': player_name
                        }))
                        logger.info(f"Cliente conectado: {player_name}")
                    
                    # Acción: Crear sala
                    elif action == 'create_room':
                        room_id = self.generate_room_id()
                        with self.rooms_lock:
                            self.rooms[room_id] = ChessRoom(room_id)
                        
                        with self.user_lock:
                            self.user_room[client_id] = room_id
                        
                        room = self.rooms[room_id]
                        room.add_player(websocket, player_name)
                        
                        await websocket.send(json.dumps({
                            'type': 'room_created',
                            'room_id': room_id,
                            'message': f'Sala creada. Comparte este código: {room_id}'
                        }))
                        logger.info(f"Sala creada: {room_id}")
                    
                    # Acción: Unirse a sala
                    elif action == 'join_room':
                        room_id = data.get('room_id')
                        
                        with self.rooms_lock:
                            if room_id not in self.rooms:
                                await websocket.send(json.dumps({
                                    'type': 'error',
                                    'message': 'Sala no encontrada'
                                }))
                                continue
                            
                            room = self.rooms[room_id]
                            
                            if not room.add_player(websocket, player_name):
                                await websocket.send(json.dumps({
                                    'type': 'error',
                                    'message': 'Sala llena'
                                }))
                                continue
                        
                        with self.user_lock:
                            self.user_room[client_id] = room_id
                        
                        await websocket.send(json.dumps({
                            'type': 'room_joined',
                            'room_id': room_id,
                            'player_count': room.get_player_count()
                        }))
                        
                        # Notificar al otro jugador
                        for other_ws in room.players.keys():
                            if other_ws != websocket:
                                await other_ws.send(json.dumps({
                                    'type': 'player_joined',
                                    'player_name': player_name,
                                    'player_count': room.get_player_count()
                                }))
                        
                        # Si sala está llena, iniciar juego
                        if room.get_player_count() == 2:
                            room.start_game()
                            game_state = room.game.get_state()
                            
                            for ws in room.players.keys():
                                await ws.send(json.dumps({
                                    'type': 'game_started',
                                    'state': game_state
                                }))
                        
                        logger.info(f"Jugador unido a sala {room_id}")
                    
                    # Acción: Hacer movimiento
                    elif action == 'move':
                        move_uci = data.get('move')
                        
                        with self.user_lock:
                            room_id = self.user_room.get(client_id)
                        
                        if not room_id or room_id not in self.rooms:
                            continue
                        
                        room = self.rooms[room_id]
                        if not room.game:
                            continue
                        
                        game = room.game
                        
                        # Verificar que es el turno del jugador
                        current_player_ws = game.players[game.current_turn]['websocket']
                        if current_player_ws != websocket:
                            await websocket.send(json.dumps({
                                'type': 'error',
                                'message': 'No es tu turno'
                            }))
                            continue
                        
                        success, result = game.make_move(move_uci)
                        
                        if not success:
                            await websocket.send(json.dumps({
                                'type': 'error',
                                'message': result
                            }))
                            continue
                        
                        # Enviar estado actualizado a ambos jugadores
                        game_state = game.get_state()
                        for ws in room.players.keys():
                            await ws.send(json.dumps({
                                'type': 'move_made',
                                'state': game_state,
                                'move': move_uci
                            }))
                        
                        logger.info(f"Movimiento en {room_id}: {move_uci}")
                    
                    # Acción: Obtener movimientos legales
                    elif action == 'get_legal_moves':
                        square = data.get('square')
                        
                        with self.user_lock:
                            room_id = self.user_room.get(client_id)
                        
                        if room_id and room_id in self.rooms:
                            room = self.rooms[room_id]
                            if room.game:
                                legal_moves = room.game.get_legal_moves(square)
                                await websocket.send(json.dumps({
                                    'type': 'legal_moves',
                                    'square': square,
                                    'moves': legal_moves
                                }))
                    
                    # Acción: Obtener estado
                    elif action == 'get_state':
                        with self.user_lock:
                            room_id = self.user_room.get(client_id)
                        
                        if room_id and room_id in self.rooms:
                            room = self.rooms[room_id]
                            if room.game:
                                game_state = room.game.get_state()
                                await websocket.send(json.dumps({
                                    'type': 'game_state',
                                    'state': game_state
                                }))
                
                except json.JSONDecodeError:
                    logger.error("JSON inválido recibido")
                except Exception as e:
                    logger.error(f"Error procesando mensaje: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.info("Conexión cerrada por cliente")
        except Exception as e:
            logger.error(f"Error en handler: {e}")
        finally:
            # Limpiar conexión
            with self.user_lock:
                if client_id in self.user_room:
                    room_id = self.user_room[client_id]
                    del self.user_room[client_id]
                    
                    with self.rooms_lock:
                        if room_id in self.rooms:
                            self.rooms[room_id].remove_player(websocket)
                            if self.rooms[room_id].get_player_count() == 0:
                                del self.rooms[room_id]
                                logger.info(f"Sala {room_id} eliminada")
    
    async def start(self):
        """Inicia el servidor WebSocket"""
        async with websockets.serve(self.handle_client, self.host, self.port):
            logger.info(f"ThreadChess Server corriendo en ws://{self.host}:{self.port}")
            await asyncio.Future()  # Corre indefinidamente


def main():
    """Función principal"""
    server = ChessServer(host='0.0.0.0', port=8765)
    
    def signal_handler(sig, frame):
        logger.info("Servidor cerrado")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Servidor interrumpido por usuario")


if __name__ == '__main__':
    main()
