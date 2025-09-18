import asyncio
import json
from typing import Optional, Callable, Any
from collections import deque
import time

class AIClient:
    """
    A virtual WebSocket client that runs on the server side.
    Processes messages from a queue and decides whether to respond.
    """
    
    def __init__(
        self,
        client_id: str,
        lobby_id: str,
        process_fn: Callable[[str], Optional[str]],
        silence_interval: float = 1.0
    ):
        """
        Args:
            client_id: Unique identifier for this virtual client
            lobby_id: Lobby this client belongs to
            process_fn: Function that takes a message and returns response or None (silence)
            silence_interval: How often to inject silence tokens (in seconds)
        """
        self.client_id = client_id
        self.lobby_id = lobby_id
        self.process_fn = process_fn
        self.silence_interval = silence_interval
        
        # Message queue for incoming messages
        self.message_queue = deque()
        
        # Control flags
        self.running = False
        self.task = None
        
    async def add_message(self, message: str):
        """Add a message to the processing queue"""
        self.message_queue.append(message)
    
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
        last_silence_time = time.time()
        
        while self.running:
            current_time = time.time()
            
            # Check if we should inject a silence token
            if current_time - last_silence_time >= self.silence_interval:
                self.message_queue.append("<silence>")
                last_silence_time = current_time
            
            # Process messages from queue
            if self.message_queue:
                message = self.message_queue.popleft()
                
                # Process the message (could be real message or silence token)
                response = self.process_fn(message)
                
                # If process_fn returns a response, broadcast it
                if response:
                    await broadcast_callback(self.lobby_id, response, self.client_id)
            
            # Small delay to prevent busy waiting
            await asyncio.sleep(0.1)