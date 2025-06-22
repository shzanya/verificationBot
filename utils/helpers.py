import re
from typing import Optional
import discord
from datetime import datetime, timezone

def sanitize_filename(filename: str) -> str:
    """Sanitize filename for safe file operations"""
    safe_chars = re.sub(r'[^\w\s-]', '', filename)
    return re.sub(r'[-\s]+', '_', safe_chars).strip('_')

def create_embed(
    title: str,
    description: str = "",
    color: int = 0x0099ff,
    member: Optional[discord.Member] = None
) -> discord.Embed:
    """Create a standardized Discord embed"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )
    
    if member:
        embed.set_footer(
            text=f"User: {member.display_name}",
            icon_url=member.display_avatar.url
        )
    
    return embed
