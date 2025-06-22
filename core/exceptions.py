class VerificationBotException(Exception):
    """Base exception for verification bot"""
    pass

class AudioFileNotFoundException(VerificationBotException):
    """Raised when audio file is not found"""
    pass

class RecordingException(VerificationBotException):
    """Raised when recording fails"""
    pass

class RoleException(VerificationBotException):
    """Raised when role operations fail"""
    pass
