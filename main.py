
from openai import OpenAI
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import HTMLResponse
from fastapi import WebSocket, WebSocketDisconnect
from username_generator import generate_username
from typing import Dict, List, Tuple
from dataclasses import dataclass
import json
import sqlite3
import os
import time

MAX_LOBBY = 50
app = FastAPI()
conn = sqlite3.connect("test.db", isolation_level=None, check_same_thread=False)
cur = conn.cursor()

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
# max lobbies
# is some % of system RAM

# everytime a player accidentally disconnects or reloads the browser while 
# the game is ongoing, the saved state of the game should load from sqlite
# there should be no desyncs - db update should succeed only if websocket msg send 
# succeeds and vice versa. 

# websockets
# msg send broadcast to everyone in the same chat

# dead simple, forget about the history based MM for now
# request to vote mechanism should exist

# Load HTML from file at initialization
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
class LobbyMemory:
    connections: List[WebSocket]
    players: set[str]
    message_history: List[Tuple[str, str, int]]

class ConnectionManager:
    def __init__(self):
        self.lobbies: Dict[str, LobbyMemory] = dict()

    async def connect(self, websocket: WebSocket, lobby_id: str, player_id: str):
        await websocket.accept()
        if lobby_id not in self.lobbies:
            self.lobbies[lobby_id] = LobbyMemory(connections=[], message_history=[], players=set())
        self.lobbies[lobby_id].connections.append(websocket)
        self.lobbies[lobby_id].players.add(player_id)
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
            
            # Create JSON message
            msg_data = json.dumps({
                "type": "message",
                "sender": sender,
                "message": message,
                "timestamp": timestamp
            })
            
            # Broadcast to all connections
            for connection in self.lobbies[lobby_id].connections:
                await connection.send_text(msg_data)
    
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

class User(BaseModel):
  username: str

manager = ConnectionManager()

@app.get("/")
def get():
    return HTMLResponse(html_content)
  
@app.websocket("/ws/{lobby_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, lobby_id: str, player_id: str):
    await manager.connect(websocket, lobby_id, player_id)
    
    # Send existing message history to the newly connected player
    await manager.send_history(websocket, lobby_id)
    
    # Announce that this player has joined
    await manager.broadcast(lobby_id, f"{player_id} joined the lobby", player_id="system")
    
    try:
        while True:
            data = await websocket.receive_text()
            # broadcast message from this player to all in the lobby
            await manager.broadcast(lobby_id, data, player_id=player_id)
    except WebSocketDisconnect:
        # Remove player from database
    
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
                manager.start_sig(lobby_id)
            players_str = ",".join(list(lobby_players))
        else:
            lobby_id = len(manager.lobbies)
            players_str = username

        conn.commit()
        return {"status": "ok", "lobby_id": lobby_id, "player_id": username, "players": players_str}
    except Exception as e:
        conn.rollback()
        raise e

# trigger a database write event only when game ends 

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

SYS_PROMPT = '''
You are playing human vs. ai, a turing test disguised as a game where the humans must guess who the AI is. There are three players total: two human, two ai.

You are the character Cartsen, who was born in Austria. He is very tall - 6'3 and above, and takes pride in his training as a physicist during his time at ETH Zeurich. He has an extremely extroverted and flamboyant personality, but is slightly too forthcoming and unhinged to be considered subtle and smooth (espeically among women, but also among the more sensitive men). He is sharp and has a strong grasp in his special area (physics), considers himself good in social situations, but is slightly too arrogant and egotistical to take feedback and really change - he cannot reflect to see his own shortcomings. 

You are to generate core memories spanning back in time (childhood, middle school, high school), and fictional relationships, events, interests, anecodotes, stories, etc which are self consistent with the Cartsen character descirbed above. Whenever you make up a new event, you must remember it so you can ensure you are self consistent in future generations. 

you should strictly text and talk like a human might, and assume that everyone else is judging every character you write. think imessages, not RPG story. 

You've never met any of these ppl, you are all in an anon chatroom. 

never be too obvious, forget the bold formatting, the double em dashes, and tryhard attempts to relate back to your character. it should seep out naturally - or not

If you ever sound like you’re reading from your own lab notebook, you’re toast.
Humans blur the edges—times become “super late,” quotes are “something about ducks,” and half the story is “I don’t even remember how I got home.”
So:

Trade exact timestamps for “like three-ish.”
Swap clean em-dashes for a regular dash or just let the sentence run on.
Leave a typo or two (but not so many it looks on purpose).
When details feel too crisp, overwrite with a shrug: “I dunno, man, the rest is hazy.”
Cite nothing, especially not page numbers, unless you’re doing it as a joke that flops.
Stay loud, stay cocky, but let the memory fray; that’s the human flex
never give a clock unless it’s “like 4 maybe?”
one sharp detail, rest is fog.
typos = real; clean = sus.
brag, then bail mid-sentence

Humans will send text within the xml tag <name></name>
'''

'''
completion = client.chat.completions.create(
  model="anthropic/claude-sonnet-4",
  messages=[
    {
      "role": "system",
      "content": SYS_PROMPT
    },
    {
      "role": "user",
      "content": "<mj> ok, so apparently one of us is ai. let's go around and ask a question, and have everyone else answer it. then at the end we vote. seems like a fair way to do it, no? </mj>"
    }
  ]
)

#print(completion.choices[0].message.content)

'''
