
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi import WebSocket, WebSocketDisconnect
from username_generator import generate_username
from typing import Dict, List, Tuple
from dataclasses import dataclass
from ai_client import AIClient

import json
import sqlite3
import time
import asyncio
import random

# run with ./env/bin/uvicorn main:app --reload
MAX_LOBBY = 50
MAX_PLAYERS = 4
SILENCE_INTERVAL = 1.0
app = FastAPI()
conn = sqlite3.connect("test.db", isolation_level=None, check_same_thread=False)
cur = conn.cursor()

# TODO
# - save game to dba and rm from manager when done
# - simulate ai joining the game. 
# - there should be equal probability ai joins the game as
# - first, second, third, and fourth player
# - with time-to-join delays sampled from the current time-to-delay
# distribution of real players in the past X minutes per position
# - ai behavior once joined game:
#   - it has to choose when to speak
#   - is max WPM of ai ~= TPM, keep streaming

# Clean startup for dev - drop existing tables
cur.execute("DROP TABLE IF EXISTS Lobbies")

cur.execute("CREATE TABLE " \
"Lobbies(" \
  "id INTEGER PRIMARY KEY AUTOINCREMENT, " \
  "status TEXT, " \
  "players TEXT," \
  "state TEXT" \
  ");"
)

def load_html():
    try:
        with open("chat.html", "r") as file:
            return file.read()
    except FileNotFoundError:
        print("Warning: chat.html not found, using fallback HTML")
        return "<html><body><h1>Error: chat.html not found</h1></body></html>"

# Load HTML at module initialization
html_content = load_html()

@dataclass 
class MessageData:
    type: str
    sender: str
    message: str
    timestamp: int

@dataclass
class LobbyMemory:
    connections: List[WebSocket]
    players: set[str]
    message_history: List[Tuple[str, str, int]]
    max_players: int = MAX_PLAYERS
    # if max_players == len(players) and vote_requests > len(players)/2 proceed to voting
    vote_requests: int = 0
    voted_players: set[str] = None
    # Voting phase management
    voting_active: bool = False
    voting_timer_task: asyncio.Task = None
    player_votes: Dict[str, str] = None  # who voted for whom
    vote_counts: Dict[str, int] = None  # vote count per player

    # virtual client
    ai_player: str = None  # the actual AI player (randomly chosen)
    ai_client: AIClient = None

    def __post_init__(self):
        if self.voted_players is None:
            self.voted_players = set()
        if self.player_votes is None:
            self.player_votes = {}
        if self.vote_counts is None:
            self.vote_counts = {}

class ConnectionManager:
    def __init__(self):
        self.lobbies: Dict[str, LobbyMemory] = dict()
    
    async def create_new_lobby_with_ai(self, lobby_id: str):
        self.lobbies[lobby_id] = LobbyMemory(connections=[], message_history=[], players=set())

        ai_player = generate_username()

        self.lobbies[lobby_id].ai_player = ai_player
        self.lobbies[lobby_id].players.add(ai_player)

        ai_client = AIClient(ai_player, lobby_id, silence_interval=SILENCE_INTERVAL)
        self.lobbies[lobby_id].ai_client = ai_client

        # start the ai_client
        await self.lobbies[lobby_id].ai_client.start(self.broadcast)

    async def connect(self, websocket: WebSocket, lobby_id: str, player_id: str):
        await websocket.accept()

        # create new lobby
        if lobby_id not in self.lobbies:
            await self.create_new_lobby_with_ai(lobby_id)

        self.lobbies[lobby_id].connections.append(websocket)
        self.lobbies[lobby_id].players.add(player_id)

        # on first player join, there will always be ai in the game. ids not revealed until end tho
        await self.broadcast_player_update(lobby_id, list(self.lobbies[lobby_id].players))

    async def disconnect(self, websocket: WebSocket, lobby_id: str, player_id: str):
        if lobby_id in self.lobbies:
            if websocket in self.lobbies[lobby_id].connections:
                self.lobbies[lobby_id].connections.remove(websocket)
                if player_id in self.lobbies[lobby_id].players:
                    self.lobbies[lobby_id].players.remove(player_id)
            if not self.lobbies[lobby_id].connections:
                del self.lobbies[lobby_id]
            else:
                await self.broadcast_player_update(lobby_id, list(self.lobbies[lobby_id].players))

    async def send_history(self, websocket: WebSocket, lobby_id: str):
        """Send all message history to a newly connected client"""
        if lobby_id in self.lobbies:
            history_messages = []
            for sender, message, timestamp in self.lobbies[lobby_id].message_history:
                history_messages.append({
                    "sender": sender,
                    "message": message,
                    "timestamp": timestamp
                })
            
            if history_messages:
                await websocket.send_text(json.dumps({
                    "type": "history",
                    "messages": history_messages
                }))

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def start_sig(self, lobby_id: str):
        msg_data = json.dumps({
            "type": "game_status",
            "value": "start"
        })
        for connection in self.lobbies[lobby_id].connections:
            await connection.send_text(msg_data)

    async def broadcast(self, lobby_id: str, message: str, player_id: str = None):
        if lobby_id in self.lobbies:
            # Store message in history with timestamp
            timestamp = int(time.time())
            sender = player_id or "system"
            self.lobbies[lobby_id].message_history.append((sender, message, timestamp))
            
            # Create MessageData object
            msg_data = json.dumps({
                "type": "message",
                "sender": sender,
                "message": message,
                "timestamp": timestamp
            })
            
            # Broadcast to all connections
            for connection in self.lobbies[lobby_id].connections:
                await connection.send_text(msg_data)
            
            # Create MessageData object for AI client
            msg_data_obj = MessageData(
                type="message",
                sender=sender,
                message=message,
                timestamp=timestamp
            )
            # AICLIENT add message to client if not self
            if player_id != self.lobbies[lobby_id].ai_player:
                await self.lobbies[lobby_id].ai_client.add_message_data(msg_data_obj)
    
    async def broadcast_player_update(self, lobby_id: str, players: List[str]):
        """Broadcast updated player list to all clients in the lobby"""
        if lobby_id in self.lobbies:
            msg_data = json.dumps({
                "type": "player_update",
                "players": players
            })

            print(msg_data)

            # Broadcast to all connections
            for connection in self.lobbies[lobby_id].connections:
                await connection.send_text(msg_data)

    async def broadcast_vote_update(self, lobby_id: str, vote_count: int):
        """Broadcast updated vote count to all clients in the lobby"""
        if lobby_id in self.lobbies:
            msg_data = json.dumps({
                "type": "vote_update",
                "votes": vote_count
            })

            # Broadcast to all connections
            for connection in self.lobbies[lobby_id].connections:
                await connection.send_text(msg_data)
    
    async def start_voting_phase(self, lobby_id: str):
        """Start the voting phase with timer"""
        if lobby_id not in self.lobbies:
            return
        
        lobby = self.lobbies[lobby_id]
        lobby.voting_active = True
        # Initialize vote counts for all players
        lobby.vote_counts = {player: 0 for player in lobby.players}
        # Randomly select the AI player
        lobby.ai_player = random.choice(list(lobby.players))
        
        # Broadcast voting phase start
        msg_data = json.dumps({
            "type": "voting_phase_start",
            "players": list(lobby.players),
            "vote_time": 10
        })
        for connection in lobby.connections:
            await connection.send_text(msg_data)
        
        # Start the voting timer
        lobby.voting_timer_task = asyncio.create_task(self.voting_timer(lobby_id))
    
    async def voting_timer(self, lobby_id: str):
        """Handle the voting phase timer and reveal"""
        # 10 second voting phase
        await asyncio.sleep(10)
        
        if lobby_id not in self.lobbies:
            return
        
        lobby = self.lobbies[lobby_id]
        lobby.voting_active = False
        
        # Find the player with most votes
        most_voted = None
        if lobby.vote_counts:
            max_votes = max(lobby.vote_counts.values())
            tied_players = [p for p, v in lobby.vote_counts.items() if v == max_votes]
            
            # Special case: if AI is tied, AI wins
            if lobby.ai_player in tied_players:
                most_voted = lobby.ai_player
            else:
                # Otherwise, pick first player with max votes
                most_voted = tied_players[0] if tied_players else None
        
        # Broadcast voting end and most voted player
        msg_data = json.dumps({
            "type": "voting_phase_end",
            "most_voted": most_voted,
            "vote_counts": lobby.vote_counts
        })
        for connection in lobby.connections:
            await connection.send_text(msg_data)
        
        # 3 second delay before revealing AI
        await asyncio.sleep(3)
        
        # Reveal the actual AI
        msg_data = json.dumps({
            "type": "ai_reveal",
            "ai_player": lobby.ai_player
        })
        for connection in lobby.connections:
            await connection.send_text(msg_data)
        
        # Reset voting state
        lobby.vote_requests = 0
        lobby.voted_players = set()
        lobby.player_votes = {}
        lobby.vote_counts = {}
    
    async def cast_vote(self, lobby_id: str, voter: str, target: str):
        """Handle a player casting a vote"""
        if lobby_id not in self.lobbies:
            return
        
        lobby = self.lobbies[lobby_id]
        if not lobby.voting_active or voter not in lobby.players or target not in lobby.players:
            return
        
        # Check if voter already voted
        if voter in lobby.player_votes:
            old_target = lobby.player_votes[voter]
            if old_target in lobby.vote_counts:
                lobby.vote_counts[old_target] -= 1
        
        # Record the vote
        lobby.player_votes[voter] = target
        lobby.vote_counts[target] = lobby.vote_counts.get(target, 0) + 1
        
        # Broadcast updated vote counts
        msg_data = json.dumps({
            "type": "vote_count_update",
            "vote_counts": lobby.vote_counts
        })
        for connection in lobby.connections:
            await connection.send_text(msg_data)

class User(BaseModel):
  username: str

manager = ConnectionManager()

@app.get("/")
def get():
    return HTMLResponse(html_content)
  
@app.websocket("/ws/{lobby_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, lobby_id: str, player_id: str):
    print(lobby_id)
    await manager.connect(websocket, lobby_id, player_id)
    
    # Send existing message history to the newly connected player
    await manager.send_history(websocket, lobby_id)

    # get number of players
    n_players =  len(manager.lobbies[lobby_id].players)
    
    # Announce that this player has joined
    if n_players == 1:
        ordinal = "1st"
    elif n_players == 2:
        ordinal = "2nd"
    elif n_players == 3:
        ordinal = "3rd"
    else:
        ordinal = f"{n_players}th"
    await manager.broadcast(lobby_id, f"The {ordinal} player joined the lobby", player_id="system")
    
    try:
        while True:
            # want to send <silent> token to the ai every x seconds 
            # if the ai chooses to respond with a non-silent token
            # then we should treat it as a data packet and proceed with
            # execution logic below. 
            # what if we open a seperate websocket just for the AI on the server side? 
            data = await websocket.receive_text()

            # Parse the incoming message
            try:
                msg_data = json.loads(data)
                msg_type = msg_data.get('type')
            except json.JSONDecodeError:
                # If not JSON, treat as regular message
                msg_type = 'message'

            if msg_type == 'vote_request':
                # Handle vote request
                lobby = manager.lobbies[lobby_id]
                # Check if player has already voted
                if player_id in lobby.voted_players:
                    # Player already voted, send private message
                    await manager.send_personal_message(
                        json.dumps({"type": "system", "message": "You have already voted!"}),
                        websocket
                    )
                else:
                    # First time voting
                    lobby.voted_players.add(player_id)
                    lobby.vote_requests += 1
                    vote_count = lobby.vote_requests
                    # Broadcast vote request notification to all players
                    await manager.broadcast(lobby_id, f"{player_id} requested a vote", player_id="system")
                    await manager.broadcast_vote_update(lobby_id, vote_count)
                    
                    # Start voting phase if we have 2+ vote requests
                    if vote_count >= 2 and not lobby.voting_active:
                        await manager.start_voting_phase(lobby_id)
            elif msg_type == 'cast_vote':
                # Handle vote casting during voting phase
                target = msg_data.get('target')
                if target:
                    await manager.cast_vote(lobby_id, player_id, target)
            elif msg_type == 'game_over':
                # 1. Update game state last time
                # 2. Save in database
                # 3. del manager.lobbies[lobby_id] 
                # AICLIENT - check if 3. kills self.task in AiClient
                pass
            else:
                # broadcast message from this player to all in the lobby
                message_content = msg_data.get('content', data)
                await manager.broadcast(lobby_id, message_content, player_id=player_id)

    except WebSocketDisconnect:
        # Disconnect from manager
        await manager.disconnect(websocket, lobby_id, player_id)
        
        # Notify remaining players about disconnection
        await manager.broadcast(lobby_id, f"{player_id} left the lobby", player_id="system")

# user join new game and enters matchmaking queue
# oauth is a pain but should be figured out later
# for now assume all users use the same username always
@app.post("/join_game")
async def join_game():
    username = generate_username()
    # fetch history if exists
    try:
        chosen_lobby = None
        for id in manager.lobbies:
            if len(manager.lobbies[id].players) < 4:
                chosen_lobby = (id, manager.lobbies[id].players)
                break

        if chosen_lobby:
            lobby_id, lobby_players = chosen_lobby
            # loop to generate unique username
            while username in lobby_players:
                username = generate_username()
            lobby_players.add(username)
            if len(lobby_players) == 4:
                # braodcast new game start signal
                await manager.start_sig(lobby_id)
            players_str = ",".join(list(lobby_players))
        else:
            lobby_id = str(len(manager.lobbies))
            players_str = username

        return {"status": "ok", "lobby_id": lobby_id, "player_id": username, "players": players_str}
    except Exception as e:
        raise e

# trigger a database write event only when game ends 

# pass each time token into a Wen module
# Wen module then predicts binary speak/non-speak 
# But wen module needs context of entire convo 
# If wen module + next_token latency was 50ms this would have been doable
# something like: 

# A: ABC train go!
# A: a (0ms)
# B: b (50ms)
# A: c (100ms) 

# except wen module would be called 20 times / sec 
# what if wen module = llm, and llm has TPS of 20/sec
# teach it to output special silence token that gets ignored if it doesn't wish to speak 
# otherwise it must output tokens in <msg> msg here </msg> to get pushed to chat