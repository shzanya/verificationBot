"""
Discord Voice Verification Bot
A professional bot for voice-based user verification
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent))

from core.bot import VerificationBot
from utils.logger import logger

def main():
    """Main entry point"""
    logger.info("Запуск бота голосовой верификации Discord...")
    
    # Create and run bot
    bot = VerificationBot()
    bot.run_bot()

if __name__ == "__main__":
    main()
