from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import discord
from config.constants import VerificationStatus

@dataclass
class VerificationSession:
    """Represents a verification session for a user"""
    user_id: int
    guild_id: int
    current_question_index: int = 0
    status: VerificationStatus = VerificationStatus.PENDING
    start_time: datetime = field(default_factory=datetime.utcnow)
    completed_questions: List[str] = field(default_factory=list)
    audio_files: List[str] = field(default_factory=list)
    
    @property
    def is_completed(self) -> bool:
        return self.status == VerificationStatus.COMPLETED
    
    @property
    def is_in_progress(self) -> bool:
        return self.status == VerificationStatus.IN_PROGRESS
    
    def next_question(self) -> None:
        """Move to the next question"""
        self.current_question_index += 1
    
    def complete(self) -> None:
        """Mark session as completed"""
        self.status = VerificationStatus.COMPLETED
