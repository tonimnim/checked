"""
Web Push Notification Service using VAPID

Native browser push notifications for Web and PWA.
No Firebase needed - uses standard Web Push Protocol.

Setup:
1. Generate VAPID keys:
   python -c "from pywebpush import webpush; from cryptography.hazmat.primitives.asymmetric import ec; from cryptography.hazmat.backends import default_backend; import base64; private_key = ec.generate_private_key(ec.SECP256R1(), default_backend()); print('Private:', base64.urlsafe_b64encode(private_key.private_numbers().private_value.to_bytes(32, 'big')).decode()); print('Public:', base64.urlsafe_b64encode(private_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode())"

   OR use: npx web-push generate-vapid-keys

2. Set in .env:
   VAPID_PUBLIC_KEY=your_public_key
   VAPID_PRIVATE_KEY=your_private_key
   VAPID_CONTACT_EMAIL=admin@chesskenya.com
"""
import json
from typing import Optional, List, Dict, Any

try:
    from pywebpush import webpush, WebPushException
    WEBPUSH_AVAILABLE = True
except ImportError:
    WEBPUSH_AVAILABLE = False
    print("[PUSH] pywebpush not installed. Run: pip install pywebpush")

from app.config import get_settings

settings = get_settings()


class WebPushService:
    """
    Web Push API service using VAPID authentication.

    Works with any modern browser (Chrome, Firefox, Edge, Safari 16+).
    Perfect for PWA - notifications work even when browser is closed.
    """

    def __init__(self):
        self.vapid_private_key = settings.vapid_private_key
        self.vapid_public_key = settings.vapid_public_key
        self.vapid_email = settings.vapid_contact_email

        self.initialized = bool(
            WEBPUSH_AVAILABLE and
            self.vapid_private_key and
            self.vapid_public_key
        )

        if self.initialized:
            print(f"[PUSH] Web Push initialized with VAPID")
        else:
            if not WEBPUSH_AVAILABLE:
                print("[PUSH] pywebpush not installed")
            elif not self.vapid_private_key or not self.vapid_public_key:
                print("[PUSH] VAPID keys not configured")

    def get_public_key(self) -> Optional[str]:
        """Get VAPID public key for frontend subscription"""
        return self.vapid_public_key if self.initialized else None

    async def send_notification(
        self,
        subscription: Dict[str, Any],
        title: str,
        body: str,
        icon: Optional[str] = "/icon-192.png",
        badge: Optional[str] = "/badge-72.png",
        url: Optional[str] = None,
        tag: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None
    ) -> dict:
        """
        Send push notification to a single subscription.

        Args:
            subscription: Push subscription object from frontend
                {
                    "endpoint": "https://fcm.googleapis.com/...",
                    "keys": {
                        "p256dh": "...",
                        "auth": "..."
                    }
                }
            title: Notification title
            body: Notification body text
            icon: Icon URL (shown in notification)
            badge: Badge URL (small icon for mobile)
            url: URL to open when notification clicked
            tag: Tag for grouping/replacing notifications
            data: Additional data payload

        Returns:
            dict with success status
        """
        if not self.initialized:
            print(f"[PUSH] Not initialized - would send: {title}")
            return {"success": False, "error": "Push service not configured"}

        try:
            payload = json.dumps({
                "title": title,
                "body": body,
                "icon": icon,
                "badge": badge,
                "url": url or "/",
                "tag": tag,
                "data": data or {}
            })

            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=self.vapid_private_key,
                vapid_claims={
                    "sub": f"mailto:{self.vapid_email}"
                }
            )

            return {"success": True}

        except WebPushException as e:
            error_msg = str(e)

            # Handle expired/invalid subscriptions
            if e.response and e.response.status_code in [404, 410]:
                return {
                    "success": False,
                    "error": "subscription_expired",
                    "should_remove": True
                }

            print(f"[PUSH] WebPush error: {error_msg}")
            return {"success": False, "error": error_msg}

        except Exception as e:
            print(f"[PUSH] Error: {e}")
            return {"success": False, "error": str(e)}

    async def send_to_multiple(
        self,
        subscriptions: List[Dict[str, Any]],
        title: str,
        body: str,
        **kwargs
    ) -> dict:
        """
        Send notification to multiple subscriptions.

        Returns:
            dict with success count and failed subscriptions
        """
        if not subscriptions:
            return {"success": True, "sent": 0, "failed": 0}

        sent = 0
        failed = 0
        expired_subscriptions = []

        for sub in subscriptions:
            result = await self.send_notification(sub, title, body, **kwargs)
            if result.get("success"):
                sent += 1
            else:
                failed += 1
                if result.get("should_remove"):
                    expired_subscriptions.append(sub)

        return {
            "success": True,
            "sent": sent,
            "failed": failed,
            "expired_subscriptions": expired_subscriptions
        }

    def is_configured(self) -> bool:
        """Check if push service is properly configured"""
        return self.initialized


# Singleton instance
push_service = WebPushService()


# Notification helpers for tournament events

async def notify_pairing_push(
    subscription: Dict[str, Any],
    opponent_username: str,
    tournament_name: str,
    color: str,
    round_number: int,
    tournament_id: str,
    pairing_id: str
):
    """Send push when player is paired"""
    return await push_service.send_notification(
        subscription=subscription,
        title=f"You're playing {opponent_username}!",
        body=f"Round {round_number} of {tournament_name}. You play as {color}.",
        url=f"/tournaments/{tournament_id}/pairings/{pairing_id}",
        tag=f"pairing-{pairing_id}",
        data={
            "type": "pairing_created",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )


async def notify_round_started_push(
    subscriptions: List[Dict[str, Any]],
    tournament_name: str,
    round_number: int,
    tournament_id: str
):
    """Send push when new round starts"""
    return await push_service.send_to_multiple(
        subscriptions=subscriptions,
        title=f"Round {round_number} is ready!",
        body=f"Check your pairing in {tournament_name}",
        url=f"/tournaments/{tournament_id}",
        tag=f"round-{tournament_id}-{round_number}",
        data={
            "type": "round_started",
            "tournament_id": tournament_id,
            "round": round_number
        }
    )


async def notify_result_push(
    subscription: Dict[str, Any],
    tournament_name: str,
    result_text: str,
    tournament_id: str,
    pairing_id: str
):
    """Send push when game result is recorded"""
    return await push_service.send_notification(
        subscription=subscription,
        title="Game result recorded",
        body=f"{tournament_name}: {result_text}",
        url=f"/tournaments/{tournament_id}",
        tag=f"result-{pairing_id}",
        data={
            "type": "result_submitted",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )


async def notify_no_show_push(
    subscription: Dict[str, Any],
    tournament_name: str,
    tournament_id: str,
    pairing_id: str
):
    """Send push when opponent claims no-show"""
    return await push_service.send_notification(
        subscription=subscription,
        title="No-show claim against you!",
        body=f"Your opponent in {tournament_name} claims you didn't show. Submit game URL to dispute.",
        url=f"/tournaments/{tournament_id}/pairings/{pairing_id}",
        tag=f"noshow-{pairing_id}",
        data={
            "type": "no_show_claimed",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )


async def notify_deadline_warning_push(
    subscription: Dict[str, Any],
    tournament_name: str,
    hours_remaining: int,
    tournament_id: str,
    pairing_id: str
):
    """Send push when deadline approaching"""
    return await push_service.send_notification(
        subscription=subscription,
        title="Game deadline approaching!",
        body=f"You have {hours_remaining}h to submit your game in {tournament_name}",
        url=f"/tournaments/{tournament_id}/pairings/{pairing_id}",
        tag=f"deadline-{pairing_id}",
        data={
            "type": "deadline_warning",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )


# In-person tournament result confirmation notifications

async def notify_result_claim_push(
    subscription: Dict[str, Any],
    claimer_username: str,
    claimed_result: str,
    tournament_name: str,
    tournament_id: str,
    pairing_id: str,
    minutes_to_confirm: int
):
    """Send push to opponent when result is claimed - needs confirmation"""
    # Convert result to readable format
    result_text = {
        "white_wins": f"{claimer_username} claims White won",
        "black_wins": f"{claimer_username} claims Black won",
        "draw": f"{claimer_username} claims Draw"
    }.get(claimed_result, f"{claimer_username} claims {claimed_result}")

    return await push_service.send_notification(
        subscription=subscription,
        title="Confirm game result",
        body=f"{result_text}. Confirm within {minutes_to_confirm} min.",
        url=f"/tournaments/{tournament_id}",
        tag=f"confirm-{pairing_id}",
        data={
            "type": "result_claim",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id,
            "action": "confirm_result"
        }
    )


async def notify_result_confirmed_push(
    subscription: Dict[str, Any],
    confirmer_username: str,
    result: str,
    tournament_name: str,
    tournament_id: str
):
    """Send push to claimer when opponent confirms the result"""
    result_text = {
        "white_wins": "White wins",
        "black_wins": "Black wins",
        "draw": "Draw"
    }.get(result, result)

    return await push_service.send_notification(
        subscription=subscription,
        title="Result confirmed!",
        body=f"{confirmer_username} confirmed: {result_text}",
        url=f"/tournaments/{tournament_id}",
        tag=f"confirmed-{tournament_id}",
        data={
            "type": "result_confirmed",
            "tournament_id": tournament_id
        }
    )


async def notify_result_disputed_push(
    subscription: Dict[str, Any],
    disputer_username: str,
    tournament_name: str,
    tournament_id: str,
    pairing_id: str,
    reason: str
):
    """Send push to claimer when opponent disputes the result"""
    return await push_service.send_notification(
        subscription=subscription,
        title="Result disputed!",
        body=f"{disputer_username} disputes your claim. Arbiter will review.",
        url=f"/tournaments/{tournament_id}",
        tag=f"disputed-{pairing_id}",
        data={
            "type": "result_disputed",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )


async def notify_admin_disputed_push(
    subscription: Dict[str, Any],
    tournament_name: str,
    tournament_id: str,
    pairing_id: str,
    white_username: str,
    black_username: str
):
    """Send push to admin when a result is disputed and needs resolution"""
    return await push_service.send_notification(
        subscription=subscription,
        title="Result dispute needs resolution",
        body=f"{tournament_name}: {white_username} vs {black_username}",
        url=f"/ck-sudo-7b3x9/tournaments/{tournament_id}",
        tag=f"admin-dispute-{pairing_id}",
        data={
            "type": "admin_dispute",
            "tournament_id": tournament_id,
            "pairing_id": pairing_id
        }
    )
