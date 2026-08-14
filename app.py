#!/usr/bin/env python3
"""
ThreadChess - Servidor Web Flask + WebSocket
Combina el servidor HTTP (Flask) con el servidor WebSocket
"""

import asyncio
import threading
import logging
from flask import Flask, send_file
from threadchess_server import ChessServer

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ThreadChess-App')

# Crear app Flask
app = Flask(__name__, static_folder='.', static_url_path='')

# Variable global para el servidor de ajedrez
chess_server = None
server_thread = None


@app.route('/')
def index():
    """Sirve el archivo principal HTML"""
    return send_file('index.html')


@app.route('/health')
def health():
    """Health check endpoint"""
    return {'status': 'ok'}, 200


def run_chess_server():
    """Ejecuta el servidor de ajedrez en un thread separado"""
    global chess_server
    chess_server = ChessServer(host='0.0.0.0', port=8765)
    
    try:
        asyncio.run(chess_server.start())
    except KeyboardInterrupt:
        logger.info("Servidor de ajedrez detenido")


def start_servers():
    """Inicia ambos servidores"""
    global server_thread
    
    # Iniciar servidor WebSocket en thread separado
    server_thread = threading.Thread(target=run_chess_server, daemon=True)
    server_thread.start()
    logger.info("Servidor WebSocket iniciado en thread separado")


if __name__ == '__main__':
    import os
    
    # Iniciar servidor de ajedrez
    start_servers()
    
    # Obtener puerto desde variable de entorno o usar 5000
    port = int(os.environ.get('PORT', 5000))
    
    # Iniciar servidor Flask
    logger.info(f"ThreadChess iniciando en puerto {port}")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        use_reloader=False,
        threaded=True
    )
