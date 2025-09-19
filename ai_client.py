from openai import OpenAI
from typing import Optional, Callable, Any
from collections import deque
from dotenv import load_dotenv

import time
import asyncio
import json
import os

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

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)
def ai_process():
    pass

class AIClient:
    """
    A virtual WebSocket client that runs on the server side.
    Processes messages from a queue and decides whether to respond.
    """
    
    def __init__(
        self,
        client_id: str,
        lobby_id: str,
        process_fn: Callable[[str], Optional[str]] = None,
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
        if not process_fn:
            self.process_fn = ai_process
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