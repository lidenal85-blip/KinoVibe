import os
import sys
import time
import sqlite3
import requests
from pathlib import Path
from core.key_pool import get_pool

class MetaAgent:
    def __init__(self):
        db_path = Path('/var/www/kinovibe/data/agent_core.db')
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        self.cursor = self.db.cursor()
        
        # Автоматическое создание таблиц, если их нет
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                message TEXT
            )
        ''')
        self.db.commit()
        print(">> [SYSTEM]: SQLite (agent_core.db) успешно подключена вместо MariaDB.")

        self.pool = get_pool()
        self.serper_key = os.getenv("SERPER_API_KEY")

    def log_action(self, level, message):
        try:
            self.cursor.execute("INSERT INTO agent_logs (level, message) VALUES (?, ?)", (level, message))
            self.db.commit()
        except Exception as e:
            print(f"Ошибка записи лога: {e}")

    def _ask_gemini(self, system_instruction, user_data):
        """Запрос к Gemini 2.5 Flash через KeyPool Левиафана"""
        try:
            entry, provider = self.pool.get_best(prefer="gemini")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={entry.value}"
            
            payload = {
                "contents": [{"parts": [{"text": f"{system_instruction}\n\nCONTEXT/ERROR:\n{user_data[:8000]}"}]}]
            }
            
            t0 = time.monotonic()
            r = requests.post(url, json=payload, timeout=30)
            latency = time.monotonic() - t0
            
            if r.status_code == 200:
                res = r.json()
                self.pool.report(entry, 200, latency=latency)
                return res['candidates'][0]['content']['parts'][0]['text']
            else:
                self.pool.report(entry, r.status_code, latency=latency)
                return None
        except Exception as e:
            print(f"[MetaAgent LLM Error]: {e}")
            return None
