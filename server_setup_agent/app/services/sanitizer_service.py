import re

class SanitizerService:
    @staticmethod
    def sanitize_command(command: str) -> str:
        """
        Cleans and normalizes the command.
        Removes known dangerous flags.
        """
        # Trim whitespace
        cmd = command.strip()
        
        # Remove dangerous flags commonly used to bypass safety
        cmd = cmd.replace("--no-preserve-root", "")
        
        # Remove multiple spaces to normalize
        cmd = re.sub(r'\s+', ' ', cmd).strip()
        
        return cmd
