from enum import Enum

class VerificationStatus(Enum):
    """Verification session statuses"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class EmbedColors:
    """Discord embed colors"""
    SUCCESS = 0x00ff00
    ERROR = 0xff0000
    WARNING = 0xffa500
    INFO = 0x0099ff
    RECORDING = 0x9932cc
