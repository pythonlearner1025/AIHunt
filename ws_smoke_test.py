import asyncio
import json

import httpx
import websockets


async def main():
    async with httpx.AsyncClient() as client:
        r = await client.post("http://127.0.0.1:8000/join_game")
        r.raise_for_status()
        data = r.json()
        lobby_id = str(data["lobby_id"])  # ensure string
        player_id = data["player_id"]

    uri = f"ws://127.0.0.1:8000/ws/{lobby_id}/{player_id}"
    async with websockets.connect(uri) as ws:
        # Read a few incoming server messages
        for _ in range(2):
            msg = await ws.recv()
            print("recv:", msg)

        # Send one player message
        await ws.send(json.dumps({"type": "message", "content": "hello from smoke test"}))
        print("sent: hello")

        # Wait to allow AI silence ticks and processing loop to run
        await asyncio.sleep(3)

        # Close
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())


