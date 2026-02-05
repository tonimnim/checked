"""
Africa's Talking SMS Service

Handles OTP and notification SMS sending.
Works with both Sandbox (testing) and Live (production) environments.

Sandbox: username="sandbox", use simulator at https://simulator.africastalking.com
Live: use your actual username and API key
"""
import africastalking
from typing import Optional
from app.config import get_settings

settings = get_settings()


class SMSService:
    """
    Africa's Talking SMS wrapper.

    Environment variables:
    - AT_USERNAME: "sandbox" for testing, your username for production
    - AT_API_KEY: Your API key (sandbox or live)
    """

    def __init__(self):
        self.username = settings.at_username
        self.api_key = settings.at_api_key
        self.sender_id = settings.at_sender_id  # Optional shortcode/sender ID
        self.initialized = False

        if self.username and self.api_key:
            try:
                africastalking.initialize(self.username, self.api_key)
                self.sms = africastalking.SMS
                self.initialized = True
                print(f"[SMS] Africa's Talking initialized (mode: {self.username})")
            except Exception as e:
                print(f"[SMS] Failed to initialize Africa's Talking: {e}")

    async def send_otp(self, phone: str, otp: str) -> dict:
        """
        Send OTP via SMS.

        Args:
            phone: Phone number in format +254XXXXXXXXX
            otp: The 6-digit OTP code

        Returns:
            dict with success status and message
        """
        if not self.initialized:
            print(f"[SMS] Not initialized - OTP {otp} would be sent to {phone}")
            return {
                "success": False,
                "error": "SMS service not configured",
                "debug_otp": otp  # For testing when SMS not configured
            }

        message = f"Your ChessKenya verification code is: {otp}\n\nThis code expires in 10 minutes. Do not share it with anyone."

        return await self._send(phone, message)

    async def send_notification(self, phone: str, message: str) -> dict:
        """Send a general notification SMS"""
        if not self.initialized:
            print(f"[SMS] Not initialized - message would be sent to {phone}")
            return {"success": False, "error": "SMS service not configured"}

        return await self._send(phone, message)

    async def _send(self, phone: str, message: str) -> dict:
        """
        Internal method to send SMS.

        Africa's Talking expects phone in format +254XXXXXXXXX
        """
        try:
            # Ensure phone starts with +
            if not phone.startswith("+"):
                phone = f"+{phone}"

            # Build kwargs
            kwargs = {
                "message": message,
                "recipients": [phone]
            }

            # Add sender ID if configured (only works in live, not sandbox)
            if self.sender_id and self.username != "sandbox":
                kwargs["sender_id"] = self.sender_id

            # Send SMS (synchronous call, but lightweight)
            response = self.sms.send(**kwargs)

            # Parse response
            recipients = response.get("SMSMessageData", {}).get("Recipients", [])

            if recipients:
                recipient = recipients[0]
                status = recipient.get("status", "")

                if status == "Success":
                    return {
                        "success": True,
                        "message_id": recipient.get("messageId"),
                        "cost": recipient.get("cost")
                    }
                else:
                    return {
                        "success": False,
                        "error": recipient.get("status", "Unknown error")
                    }

            return {
                "success": False,
                "error": "No recipients in response"
            }

        except Exception as e:
            print(f"[SMS] Error sending to {phone}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def is_configured(self) -> bool:
        """Check if SMS service is properly configured"""
        return self.initialized


# Singleton instance
sms_service = SMSService()
