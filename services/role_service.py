import discord
from typing import Optional
from utils.logger import logger
from core.exceptions import RoleException

class RoleService:
    """Service for managing user roles"""
    
    @staticmethod
    async def assign_verified_role(
        member: discord.Member,
        verified_role_id: int,
        unverified_role_id: Optional[int] = None
    ) -> bool:
        """Assign verified role and remove unverified role"""
        try:
            guild = member.guild
            verified_role = guild.get_role(verified_role_id)
            
            if not verified_role:
                raise RoleException(f"Verified role not found: {verified_role_id}")
            
            # Add verified role if not already present
            if verified_role not in member.roles:
                await member.add_roles(verified_role, reason="Passed voice verification")
                logger.info(f"Assigned verified role to {member.display_name}")
            
            # Remove unverified role if specified and present
            if unverified_role_id:
                unverified_role = guild.get_role(unverified_role_id)
                if unverified_role and unverified_role in member.roles:
                    await member.remove_roles(unverified_role, reason="Completed verification")
                    logger.info(f"Removed unverified role from {member.display_name}")
            
            return True
            
        except Exception as e:
            logger.error(f"Role assignment failed for {member.display_name}: {e}")
            raise RoleException(f"Failed to assign roles: {e}")
    
    @staticmethod
    async def kick_member_after_verification(
        member: discord.Member,
        reason: str = "Completed verification process"
    ) -> bool:
        """Kick member after successful verification"""
        try:
            await member.kick(reason=reason)
            logger.info(f"Kicked {member.display_name} after verification")
            return True
            
        except Exception as e:
            logger.error(f"Failed to kick {member.display_name}: {e}")
            return False
