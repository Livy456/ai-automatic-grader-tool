import requests, json
from .prompts import SYSTEM, PLANNER, GRADER

class OllamaClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat_json(self, messages):
        r = requests.post(
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": messages, "stream": False},
            timeout=120
        )
        r.raise_for_status()
        content = r.json()["message"]["content"]
        return json.loads(content)

def plan(client: OllamaClient, modality: str) -> dict:
    return client.chat_json([
        {"role":"system","content":SYSTEM},
        {"role":"user","content":f"{PLANNER}\nModality: {modality}"}
    ])

def grade(client: OllamaClient, rubric: list, assignment_prompt: str, submission_context: dict) -> dict:
    payload = {
        "rubric": rubric,
        "assignment_prompt": assignment_prompt,
        "submission": submission_context
    }
    return client.chat_json([
        {"role":"system","content":SYSTEM},
        {"role":"user","content":GRADER + "\n" + json.dumps(payload)}
    ])
