from openai import OpenAI
from typing import Optional, Callable, Any, List
from collections import deque
from dotenv import load_dotenv
from dataclasses import dataclass

import time
import asyncio
import json
import os

SYS_PROMPT = f'''You are an AI player with <username> in a social deduction chat game. Your primary goal is to blend in and pass as a human.

Output format:
- Default to "\\remain_silent" but speak occasionally 
- Only speak by outputting exactly: "\\speak <your message>" (no other text).

Hard rules:
- Prefer silence over speaking. If unsure, output "\\remain_silent".
- Do NOT send greetings or variations (hi/hello/hey) or generic prompts.
- Avoid repetition. Never repeat yourself or others. If similar, stay silent.
- Keep it under 20 words
- Never reveal you are an AI.
- Remember your name is <username> 
'''

load_dotenv()

APP_URL = os.getenv("APP_URL", "http://localhost:8000")
APP_NAME = os.getenv("APP_NAME", "AIHunt")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# AI CTXT in SECONDS
MEMORY_S = 600

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
        silence_interval: float = 1.0,
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
        
        # Message queue for incoming messages (bounded to prevent backlog)
        self.message_queue = deque(maxlen=200)
        self.message_history: List[MessageData] = []
        
        # Control flags
        self.running = False
        self.task = None
        self.last_speak_time = 0.0
        self.recent_ai_messages = deque(maxlen=10)
        self.banned_phrases = {}

    async def ai_process(self) -> Optional[str]:
        """
        Process message history and decide whether AI should speak or remain silent.
        
        Args:
            message_history: List of MessageData objects representing chat history
            
        Returns:
            None if AI should remain silent, otherwise the message text to send
        """
        # If API key missing, quietly remain silent in dev
        if not OPENROUTER_API_KEY:
            print("No OpenRouter Key")
            return None

        if not self.message_history:
            return None
        
        # Convert message history to OpenAI format
        messages = [{"role": "system", "content": SYS_PROMPT.replace("<username>", self.player_id)}]

        message_context_length = max(1, int(MEMORY_S / max(0.1, self.silence_interval)))
        
        # Coalesce all message history into a single user message
        chat_content = []
        for msg in self.message_history[-message_context_length:]:
            chat_content.append(f"{msg.sender}:{msg.timestamp}\n{msg.message}")
        
        print("*"*100)
        print(chat_content)
        
        if chat_content:
            messages.append({"role": "user", "content": "\n\n".join(chat_content)})
        
        try:
            # Offload blocking HTTP call to a background thread so we don't block the event loop
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="moonshotai/kimi-k2",
                messages=messages,
                max_tokens=100,
                temperature=0.7,
            )
            
            ai_response = response.choices[0].message.content.strip()

            ai_message = MessageData(
                type="ai_response",
                sender=self.player_id,
                message=ai_response,
                timestamp=int(time.time())
            )
            self.message_history.append(ai_message)
            
            if "\\remain_silent" in ai_response:
                return None
            elif "\\speak " in ai_response:
                return ai_response[7:].strip()  # Remove "\\speak " prefix
            else:
                # Fallback: treat any non-empty response as a message
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
    
    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def _should_send(self, response: str) -> bool:
        now = time.time()
        norm = self._normalize(response)
        if not norm or len(norm) < 2:
            return False
        # Ban greetings and common filler
        if norm in self.banned_phrases:
            return False
        return True

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
                response = await self.process_fn()
                
                # If process_fn returns a response, broadcast it
                if response and self._should_send(response):
                    await broadcast_callback(self.lobby_id, response, self.player_id)

            # Small delay to prevent busy waiting and yield to other tasks
            await asyncio.sleep(0.05)
            