"""Slack slash commands router and handler"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from urllib.parse import parse_qs
from app.integrations.slack import command_handlers
from app.integrations.base.webhook_registry import require_webhook_auth

router = APIRouter(prefix="/api/integrations/slack", tags=["slack-commands"])


@router.post("/commands")
async def handle_slack_command(
    raw_body: bytes = Depends(require_webhook_auth("slack")),
):
    """
    Handle `/promarshal ...` slash commands (DM-only).

    Returns:
        JSONResponse with Slack-formatted message
    """
    try:
        # Parse form data from raw body
        form_data = parse_qs(raw_body.decode("utf-8"))

        # Extract form fields (each value is a list, take first element)
        command = form_data.get("command", [""])[0]
        text = form_data.get("text", [""])[0]
        user_id = form_data.get("user_id", [""])[0]
        team_id = form_data.get("team_id", [""])[0]
        channel_id = form_data.get("channel_id", [""])[0]
        trigger_id = form_data.get("trigger_id", [""])[0]
        response_url = form_data.get("response_url", [""])[0]
        user_name = form_data.get("user_name", [""])[0]

        # Build command payload
        payload = {
            "command": command,
            "text": text.strip(),
            "user_id": user_id,
            "team_id": team_id,
            "channel_id": channel_id,
            "trigger_id": trigger_id,
            "response_url": response_url,
            "user_name": user_name,
        }

        # Restrict slash commands to DM only to keep channel UX natural-language-first.
        if not channel_id.startswith("D"):
            response = {
                "response_type": "ephemeral",
                "text": (
                    "ProMarshal slash commands are available in DM only.\n\n"
                    "Open a DM with ProMarshal and run `/promarshal help`."
                ),
            }
            return JSONResponse(content=response)

        response = await command_handlers.dispatch_command(payload)

        return JSONResponse(content=response)

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error handling Slack command: {str(e)}")
        return JSONResponse(
            content={
                "response_type": "ephemeral",
                "text": "An error occurred. Please try again or contact support."
            },
            status_code=200  # Always return 200 to Slack
        )
