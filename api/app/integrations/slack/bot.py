"""Slack bot service for message handling and AI classification"""

import hmac
import hashlib
import time
from typing import Dict, Any, Optional
from slack_sdk.web.async_client import AsyncWebClient
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.security import encryption_service
from app.integrations.slack.delivery import ResilientSlackWebClient


class SlackBotService:
    """Service for Slack bot functionality including message classification"""

    def __init__(self):
        """Initialize Slack bot service"""
        self.signing_secret = settings.slack_signing_secret
        if settings.openai_api_key:
            self.openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        else:
            self.openai_client = None
        self.model = settings.openai_model

    def verify_slack_signature(
        self,
        signature: str,
        timestamp: str,
        body: bytes
    ) -> bool:
        """
        Verify that the request came from Slack

        Args:
            signature: X-Slack-Signature header
            timestamp: X-Slack-Request-Timestamp header
            body: Raw request body

        Returns:
            True if signature is valid
        """
        if not self.signing_secret:
            print("Warning: Slack signing secret not configured")
            return True  # Allow in development

        # Check timestamp to prevent replay attacks (5 minute window)
        current_time = int(time.time())
        if abs(current_time - int(timestamp)) > 60 * 5:
            return False

        # Create signature base string
        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"

        # Calculate expected signature
        my_signature = 'v0=' + hmac.new(
            self.signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(my_signature, signature)

    async def classify_message(self, message: str) -> Dict[str, Any]:
        """
        Classify a Slack message using OpenAI

        Args:
            message: The message text to classify

        Returns:
            Classification result with category and confidence
        """
        if not self.openai_client:
            return {
                "category": "general_message",
                "confidence": 0.5,
                "reasoning": "OpenAI not configured"
            }

        try:
            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": """You are a message classifier for a project management assistant.
Classify messages into one of these categories:
- general_message: Regular conversation, greetings, questions
- action_item: Tasks that need to be done, requests for action
- project_task: Specific project-related tasks, bugs, features

Respond in JSON format:
{"category": "category_name", "confidence": 0.0-1.0, "reasoning": "brief explanation"}"""
                    },
                    {
                        "role": "user",
                        "content": f"Classify this message: {message}"
                    }
                ],
                temperature=0.3,
                max_tokens=150
            )

            import json
            result_text = response.choices[0].message.content
            return json.loads(result_text)

        except Exception as e:
            print(f"Error classifying message: {str(e)}")
            return {
                "category": "general_message",
                "confidence": 0.5,
                "reasoning": f"Classification error: {str(e)}"
            }

    def get_slack_client(self, encrypted_token: str):
        """
        Create a Slack client with decrypted token

        Args:
            encrypted_token: Encrypted Slack access token

        Returns:
            AsyncWebClient instance
        """
        decrypted_token = encryption_service.decrypt(encrypted_token)
        return ResilientSlackWebClient(AsyncWebClient(token=decrypted_token))

    def has_bot_mention(self, event: Dict[str, Any], bot_user_id: str) -> bool:
        """
        Check if the bot was mentioned in the event

        Args:
            event: Slack event data
            bot_user_id: Bot's user ID

        Returns:
            True if bot was mentioned
        """
        text = event.get("text", "")
        return f"<@{bot_user_id}>" in text

    def get_response_message(self, classification: Dict[str, Any]) -> str:
        """
        Generate a response based on message classification

        Args:
            classification: Classification result

        Returns:
            Response message string
        """
        category = classification.get("category", "general_message")

        responses = {
            "general_message": "I received your message. How can I help you with your project?",
            "action_item": "I've noted this as an action item. Would you like me to create a task in Jira?",
            "project_task": "This looks like a project task. I can help you track this in your project management tools."
        }

        return responses.get(category, responses["general_message"])

    async def get_user_by_email(self, slack_client: AsyncWebClient, email: str) -> Optional[str]:
        """
        Get Slack user ID by email address

        Args:
            slack_client: Slack web client
            email: User's email address

        Returns:
            User ID if found, None otherwise
        """
        try:
            response = await slack_client.users_lookupByEmail(email=email)
            if response.get("ok"):
                return response.get("user", {}).get("id")
            return None
        except Exception as e:
            print(f"Error looking up user by email: {str(e)}")
            return None

# Singleton instance
slack_bot_service = SlackBotService()
