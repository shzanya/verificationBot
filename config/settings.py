import os
from typing import List, Dict
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class BotSettings:
    """Bot configuration settings"""
    token: str
    command_prefix: str = "!"
    
    # Channel IDs
    voice_channel_id: int = 1378734533410689155
    text_channel_id: int = 1378763397335879832
    
    # Role IDs
    verified_role_id: int = 1376499305698951269
    unverified_role_id: int = 1376499288888053800
    
    # Verification settings
    questions: List[str] = None
    recording_durations: List[int] = None
    audio_files: Dict[str, str] = None
    
    def __post_init__(self):
        if self.questions is None:
            self.questions = [
                "Сколько тебе лет?",
                "Скажи, я хочу получить доступ к серверу Arunya"
            ]
        
        if self.recording_durations is None:
            self.recording_durations = [3, 6]  # seconds
            
        if self.audio_files is None:
            self.audio_files = {
                "Сколько тебе лет?": "assets/audio/question_1.mp3",
                "Скажи, я хочу получить доступ к серверу Arunya": "assets/audio/question_2.mp3",
                "completion": "assets/audio/completion.mp3"
            }

# Global settings instance
settings = BotSettings(
    token=os.getenv("DISCORD_TOKEN", ""),
)
