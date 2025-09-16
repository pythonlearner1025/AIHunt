import os
import re
import json
import random
from datetime import datetime
from typing import List, Tuple, Dict

import re

def blacklist(msg: str) -> bool:
    if not msg or not isinstance(msg, str):
        return True

    # Normalize
    m = msg.strip().lower()

    # Exact filler lines
    if m in {"this message responded to an earlier message."}:
        return True

    # Reaction markers (iOS style)
    if any(tag in m for tag in [
        "liked by", "loved by", "laughed by", "emphasized by",
        "disliked by", "reacted", "tapback", "removed a reaction"
    ]):
        return True

    # Edit markers
    if "edited" in m or m.startswith("edit:"):
        return True

    # Timestamps / unsent notifications
    if "unsent a message" in m or "deleted a message" in m:
        return True

    # Attachment placeholders
    if re.match(r"^attachments?/\d+/", m):
        return True

    # Generic auto-generated filler (e.g. “Sent with …”)
    if m.startswith("sent with "):
        return True

    return False


def parse_messages(file_path: str) -> List[Tuple[str, str]]:
    """Parse messages from a text file and return list of (sender, message) tuples."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    messages = []
    lines = content.strip().split('\n\n')
    for line in lines:
        try:
            sender = line.split("\n")[1].strip()
            if sender != "Me":
                sender = "Other"
            print("-"*19)
            msg = "".join(line.split("\n")[-1])
            if not blacklist(msg):
                print(msg)
                messages.append((sender, msg))
            else:
                print("SKIP")
        except:
            continue

    return messages

def extract_message_pairs(messages: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    """Extract message pairs where user is the other person and assistant is 'Me'."""
    pairs = []
    
    # Work backwards through messages
    messages = list(reversed(messages))
    
    i = 0
    while i < len(messages):
        # Find "Me" messages
        if messages[i][0] == 'Me':
            # Collect consecutive "Me" messages
            my_messages = [messages[i][1]]
            j = i + 1
            while j < len(messages) and messages[j][0] == 'Me':
                my_messages.append(messages[j][1])
                j += 1
            
            # Now find the previous other person's messages
            other_messages = []
            while j < len(messages) and messages[j][0] != 'Me':
                other_messages.append(messages[j][1])
                j += 1
            
            if other_messages:
                # Create the pair with other person as user, my response as assistant
                user_message = ' '.join(reversed(other_messages))
                assistant_response = ' '.join(reversed(my_messages))
                pairs.append({
                    'user': user_message,
                    'assistant': assistant_response
                })
            
            i = j
        else:
            i += 1
    
    # Return in chronological order
    return list(reversed(pairs))

def random_slices(lst, k=3):
    n = len(lst)
    slices = []
    for _ in range(k):
        if n < 2:
            break  # can't slice meaningfully
        i, j = sorted(random.sample(range(n + 1), 2))  # choose two cut points
        if i != j:
            if j-i > 25:
                i = j-25
            slices.append(lst[i:j])
    return slices

def quality_fail(msg: str) -> bool:
    """Soft quality filters: essays, attachment-heavy, spammy junk."""
    if not msg or not isinstance(msg, str):
        return True
    m = msg.strip().lower()

    # Too long (essay-like)
    if len(m.split()) > 150 or len(m) > 1000:
        return True

    # Too many attachments
    if sum(m.count(ext) for ext in [".jpg", ".jpeg", ".png", ".heic", ".mov"]) > 1:
        return True

    # Ad / bio spam heuristics
    if any(tag in m for tag in [
        "follow me on instagram", "check out this home on airbnb",
        "likes,", "comments -", "menu offers a variety"
    ]):
        return True

    return False

def create_fine_tuning_dataset(top_files: List[str], examples_per_file: int = 3) -> List[Dict]:
    dataset = []

    for file_path in top_files:
        try:
            messages = parse_messages(file_path)
            pairs = extract_message_pairs(messages)

            # keep trying slices until they pass quality check
            selected = []
            attempts = 0
            while len(selected) < examples_per_file and attempts < 20:
                slices = random_slices(pairs, k=examples_per_file)
                for sl in slices:
                    all_msgs = [p['user'] for p in sl] + [p['assistant'] for p in sl]
                    if all(not quality_fail(m) for m in all_msgs):
                        selected.append(sl)
                attempts += 1

            # fallback: if still empty, allow slices but strip bad msgs
            if not selected:
                for sl in random_slices(pairs, k=examples_per_file):
                    cleaned = []
                    for p in sl:
                        if not quality_fail(p['user']) and not quality_fail(p['assistant']):
                            cleaned.append(p)
                    if cleaned:
                        selected.append(cleaned)

            # format for fine-tuning
            for sl in selected:
                msg_list = []
                for p in sl:
                    msg_list += [
                        {"role": "user", "content": p['user']},
                        {"role": "assistant", "content": p['assistant']}
                    ]
                dataset.append({"messages": msg_list})

        except Exception as e:
            print(f"Error processing {file_path}: {str(e)}")

    return dataset



def main(n_files: int = 10, examples_per_file: int = 5):
    # Get all text files from output directory
    output_dir = '/Users/minjunes/mafia/output'
    all_files = []
    
    for filename in os.listdir(output_dir):
        if filename.endswith('.txt'):
            filepath = os.path.join(output_dir, filename)
            
            # Skip group chats (files with commas indicating multiple participants)
            if ',' in filename:
                continue
                
            # Skip specific banned files
            if 'our_place' in filename.lower():
                continue
                
            # Get file size
            file_size = os.path.getsize(filepath)
            all_files.append((filepath, file_size))
    
    # Sort by size (descending) and take top n
    all_files.sort(key=lambda x: x[1], reverse=True)
    top_files = [filepath for filepath, _ in all_files[:n_files]]
    
    print(f"Selected top {n_files} conversation files:")
    for i, (filepath, size) in enumerate(all_files[:n_files], 1):
        print(f"{i}. {os.path.basename(filepath)} ({size:,} bytes)")
    
    # Create dataset with specified examples per file
    dataset = create_fine_tuning_dataset(top_files, examples_per_file=examples_per_file)
    
    # Save to JSONL format
    output_file = 'fine_tune_dataset.jsonl'
    with open(output_file, 'w', encoding='utf-8') as f:
        for item in dataset:
            f.write(json.dumps(item) + '\n')
    
    print(f"\nDataset created successfully!")
    print(f"Total examples: {len(dataset)}")
    print(f"Output file: {output_file}")


if __name__ == "__main__":
    # Can now easily adjust number of files and examples per file
    main(n_files=15, examples_per_file=5)


