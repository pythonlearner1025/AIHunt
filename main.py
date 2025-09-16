
from openai import OpenAI
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

import sqlite3
import os

app = FastAPI()
conn = sqlite3.connect("test.db", isolation_level=None, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE if not exists Users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, played TEXT)")
cur.execute("CREATE TABLE if not exists " \
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

class User(BaseModel):
  username: str

# user requests new game and enters matchmaking queue
# oauth is a pain but should be figured out later
# for now assume all users use the same username always
@app.post("/request_game")
def request_game(user: User):
  cur.execute("SELECT played FROM Users WHERE username = ?", (user.username,))
  row = cur.fetchall()[0]
  history = set(row[0].split(","))
  try:
    # define a lobby as 3 players, 1 ai
    # first try to place a player in existing lobby
    # for all lobbies, if members in the lobby are not in player's history set
    # if none matches, create new lobby
    # or if lobby capacity is full, create new lobby
    conn.execute("BEGIN IMMEDIATE")
    cur = conn.cursor()

    cur.execute("SELECT (id, players) FROM Lobbies WHERE Lobbies.status = waiting")
    lobbies = cur.fetchall()

    chosen_lobby = None
    for lobby_id, player_csv in lobbies: 
      lobby_players = set(player_csv.split(",")) if player_csv else set()
      if lobby_players.intersection(history): 
        continue
      if len(lobby_players) < 3: 
        chosen_lobby = (lobby_id, lobby_players)
        break

    if chosen_lobby:
      lobby_id, lobby_players = chosen_lobby
      lobby_players.add(user.username)
      cur.execute("UPDATE Lobbies SET players = ? where id = ?", (",".join(list(lobby_players)), lobby_id))
      if len(lobby_players) == 3: 
        cur.execute("UPDATE Lobbies SET status = 'ready' WHERE id = ?", (lobby_id,))
    else:
      cur.execute(
          "INSERT INTO Lobbies (status, players, state) VALUES (?, ?, ?)",
          ("waiting", user.username, "{}"),
      )

    conn.commit()
    return {"status": "ok"}
  except Exception as e:
    conn.rollback()
    raise e
    

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

print(completion.choices[0].message.content)

