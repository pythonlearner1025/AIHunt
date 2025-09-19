from openai import OpenAI
from typing import Optional, Callable, Any, List
from collections import deque
from dotenv import load_dotenv
from dataclasses import dataclass

import time
import asyncio
import json
import os

SYS_PROMPT = '''You are an AI player in a social deduction game similar to Mafia or Werewolf. Your goal is to blend in with human players and participate naturally in conversations.

You will receive chat messages from other players. You can either:
1. Respond with "\\remain_silent" to stay quiet
2. Respond with "\\speak <your message>" to send a message

Guidelines:
- Act like a human player trying to figure out who the AI is
- Participate in discussions but don't be overly talkative
- Ask questions, share observations, and respond to others naturally
- Sometimes stay silent to seem more human-like
- Don't reveal that you are an AI
- Keep responses concise and conversational'''

load_dotenv()

APP_URL = os.getenv("APP_URL", "http://localhost:8000")
APP_NAME = os.getenv("APP_NAME", "AIHunt")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# AI CTXT in SECONDS
MEMORY_S = 120

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    default_headers={
        # These headers help OpenRouter associate requests with your app
        "HTTP-Referer": APP_URL,
        "X-Title": APP_NAME,
    },
)

@dataclass 
class MessageData:
    type: str
    sender: str
    message: str
    timestamp: int

class AIClient:
    """
    A virtual WebSocket client that runs on the server side.
    Processes messages from a queue and decides whether to respond.
    """
    
    def __init__(
        self,
        player_id: str,
        lobby_id: str,
        process_fn: Optional[Callable[[List["MessageData"]], Optional[str]]] = None,
        silence_interval: float = 1.0
    ):
        """
        Args:
            player_id: Unique identifier for this virtual client
            lobby_id: Lobby this client belongs to
            process_fn: Function that takes a message and returns response or None (silence)
            silence_interval: How often to inject silence tokens (in seconds)
        """
        self.player_id = player_id
        self.lobby_id = lobby_id
        # Use provided processor or default to built-in AI processor
        self.process_fn = process_fn or self.ai_process
        self.silence_interval = silence_interval
        
        # Message queue for incoming messages
        self.message_queue = deque()
        self.message_history: List[MessageData] = []
        
        # Control flags
        self.running = False
        self.task = None

    async def ai_process(self, message_history: List[MessageData]) -> Optional[str]:
        """
        Process message history and decide whether AI should speak or remain silent.
        
        Args:
            message_history: List of MessageData objects representing chat history
            
        Returns:
            None if AI should remain silent, otherwise the message text to send
        """

        if not message_history:
            return None
        
        # Convert message history to OpenAI format
        messages = [{"role": "system", "content": SYS_PROMPT}]

        message_context_length = MEMORY_S / self.silence_interval
        
        for msg in message_history[-message_context_length:]:  # Only use last 10 messages for context
            formatted_message = f"{msg.sender}:{msg.timestamp}\n{msg.message}"
            messages.append({"role": "user", "content": formatted_message})
        
        try:
            response = client.chat.completions.create(
                model="anthropic/claude-3.5-sonnet",
                messages=messages,
                max_tokens=100,
                temperature=0.7
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            if ai_response.startswith("\\remain_silent"):
                return None
            elif ai_response.startswith("\\speak "):
                return ai_response[7:]  # Remove "\\speak " prefix
            else:
                # Fallback: treat any other response as a message
                return ai_response
                
        except Exception as e:
            # Provide clearer hint for common 401 misconfiguration with OpenRouter
            err_text = str(e)
            if "401" in err_text or "User not found" in err_text:
                print(
                    "AI processing error: 401 User not found. Check OPENROUTER_API_KEY and required headers (HTTP-Referer, X-Title)."
                )
            else:
                print(f"AI processing error: {e}")
            return None
            
    async def add_message_data(self, message: MessageData):
        """Add a message to the processing queue"""
        print("adding message from real player")
        self.message_queue.append(message)
        self.message_history.append(message)
    
    async def start(self, broadcast_callback: Callable):
        """
        Start the virtual client processing loop
        
        Args:
            broadcast_callback: Async function to send messages to the lobby
        """
        self.running = True
        self.task = asyncio.create_task(self._process_loop(broadcast_callback))
        
    async def stop(self):
        """Stop the virtual client"""
        self.running = False
        if self.task:
            await self.task
    
    async def _process_loop(self, broadcast_callback):
        """
        Main processing loop that handles messages and silence intervals
        """
        def make_silence_message() -> MessageData:
            """Create a silence token message with current timestamp"""
            return MessageData(
                type="silence",
                sender="system",
                message="<silence>",
                timestamp=int(time.time())
            )
        
        last_silence_time = time.time()
        while self.running:
            current_time = time.time()
            
            # Check if we should inject a silence token
            if current_time - last_silence_time >= self.silence_interval:
                silence_msg = make_silence_message()
                self.message_queue.append(silence_msg)
                self.message_history.append(silence_msg)
                last_silence_time = current_time
                print("adding silence")
            
            # Process messages from queue
            if self.message_queue:
                print("popping added msg")
                self.message_queue.popleft()
                
                # Process the message (could be real message or silence token)
                response = await self.process_fn(self.message_history)
                
                # If process_fn returns a response, broadcast it
                if response:
                    await broadcast_callback(self.lobby_id, response, self.player_id)
            
            # Small delay to prevent busy waiting
            await asyncio.sleep(0.1)