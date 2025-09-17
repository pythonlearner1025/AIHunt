import random

def generate_username():
    """Generate a random username in the format: adjective-noun123"""
    
    
    adjectives = [
        "fast","lazy","happy","angry","silent",
        "brave","clever","shy","curious","loyal",
        "wild","calm","fierce","gentle","bold",
        "mighty","tiny","jolly","wise","grumpy",
        "quick","sneaky","noisy","bright","dark",
        "sleepy","hungry","proud","crazy","chill",
        "stormy","sunny","frosty","dusty","rusty",
        "sharp","smooth","rough","strange","funny",
        "glorious","ancient","modern","epic","simple",
        "rare","common","hot","cold","warm"
    ]
    
    nouns = [
        "tiger","dragon","leaf","river","star",
        "wolf","lion","bear","eagle","hawk",
        "shark","whale","dolphin","octopus","crab",
        "tree","rock","mountain","cloud","storm",
        "sun","moon","planet","galaxy","comet",
        "flame","shadow","ghost","spirit","demon",
        "angel","wizard","knight","samurai","ninja",
        "pirate","robot","cyborg","alien","giant",
        "phoenix","griffin","unicorn","serpent","kraken",
        "fox","owl","bat","deer","panther",
        # AI-related
        "neuron","matrix","tensor","model","prompt",
        "agent","bot","server","quantum","algorithm"
    ]
    
    adj = random.choice(adjectives)
    noun = random.choice(nouns)
    num = random.randint(0, 999)
    
    return f"{adj}-{noun}{num}"


# Example usage
if __name__ == "__main__":
    # Generate and print 5 random usernames
    for _ in range(5):
        print(generate_username())
