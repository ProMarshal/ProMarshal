"""Shared email service with provider-backed transactional delivery."""

import asyncio
from typing import Any, Dict

import httpx
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.core.config import settings


def _normalized_email_provider() -> str:
    return str(settings.email_provider or "zeptomail").strip().lower()


def _normalize_email_from(raw_value: str) -> Dict[str, str]:
    cleaned = str(raw_value or "").strip()
    if not cleaned:
        return {"address": "", "name": ""}
    if "<" in cleaned and ">" in cleaned:
        name_part, remainder = cleaned.split("<", 1)
        address_part = remainder.split(">", 1)[0]
        return {
            "name": name_part.strip().strip('"'),
            "address": address_part.strip(),
        }
    return {"address": cleaned, "name": ""}


async def _send_via_zeptomail(
    *,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str,
) -> bool:
    api_url = str(settings.zeptomail_api_url or "").strip()
    api_key = str(settings.zeptomail_api_key or "").strip()
    if not api_url or not api_key:
        print(f"[EMAIL] ZeptoMail config missing. Skipping email to {to_email}")
        return False

    sender = _normalize_email_from(settings.email_from)
    if not sender["address"]:
        print(f"[EMAIL] Invalid email_from config. Skipping email to {to_email}")
        return False

    payload: Dict[str, Any] = {
        "from": {
            "address": sender["address"],
        },
        "to": [
            {
                "email_address": {
                    "address": to_email,
                }
            }
        ],
        "subject": subject,
        "htmlbody": html_content,
        "textbody": text_content,
    }
    if sender["name"]:
        payload["from"]["name"] = sender["name"]

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Authorization": f"Zoho-enczapikey {api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(api_url, headers=headers, json=payload)
        if 200 <= response.status_code < 300:
            print(f"[EMAIL] ZeptoMail delivered email to {to_email}, status: {response.status_code}")
            return True
        print(
            "[EMAIL] ZeptoMail send failed "
            f"to {to_email}, status: {response.status_code}, body: {response.text}"
        )
        return False
    except Exception as exc:
        print(f"[EMAIL] ZeptoMail exception for {to_email}: {exc}")
        return False


def _send_via_sendgrid(
    *,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str,
) -> bool:
    if not settings.sendgrid_api_key:
        print(f"[EMAIL] SendGrid API key not configured. Skipping email to {to_email}")
        return False

    try:
        message = Mail(
            from_email=settings.email_from,
            to_emails=to_email,
            subject=subject,
            plain_text_content=text_content,
            html_content=html_content,
        )
        sg = SendGridAPIClient(settings.sendgrid_api_key)
        response = sg.send(message)
        print(f"[EMAIL] SendGrid delivered email to {to_email}, status: {response.status_code}")
        return True
    except Exception as exc:
        print(f"[EMAIL] SendGrid send failed to {to_email}: {exc}")
        return False


async def _send_email(
    *,
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str,
) -> bool:
    provider = _normalized_email_provider()
    if provider == "zeptomail":
        return await _send_via_zeptomail(
            to_email=to_email,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
    if provider == "sendgrid":
        return await asyncio.to_thread(
            lambda: _send_via_sendgrid(
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            ),
        )

    print(f"[EMAIL] Unsupported email provider '{provider}'. Skipping email to {to_email}")
    return False


async def send_invite_email(
    to_email: str,
    project_name: str,
    inviter_name: str,
    invite_token: str,
    role: str = "member",
) -> bool:
    """
    Send project invitation email using the configured provider.
    Returns True if sent successfully, False otherwise.
    """

    invite_url = f"{settings.frontend_url}/invite/{invite_token}"
    role_label = str(role or "member").replace("_", " ").strip().title() or "Member"
    inviter_display = str(inviter_name or "A team member").strip() or "A team member"
    project_display = str(project_name or "Project").strip() or "Project"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f5f6f8; margin: 0; padding: 40px 20px;">
        <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 20px; padding: 44px 40px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);">
            <div style="text-align: center; margin-bottom: 32px;">
                <p style="color: #111111; font-size: 28px; font-weight: 800; letter-spacing: -0.02em; margin: 0;">ProMarshal</p>
            </div>

            <p style="color: #6b7280; font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 12px;">Project Invitation</p>

            <h2 style="color: #111827; font-size: 28px; line-height: 1.2; font-weight: 800; margin: 0 0 16px;">
                You're invited to join a project
            </h2>

            <p style="color: #4b5563; font-size: 16px; line-height: 1.7; margin: 0 0 28px;">
                <strong>{inviter_display}</strong> invited you to join <strong>{project_display}</strong> on ProMarshal as a <strong>{role_label}</strong>.
            </p>

            <div style="background-color: #f8fafc; border: 1px solid #e5e7eb; border-radius: 16px; padding: 20px 22px; margin: 0 0 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
                    <tr>
                        <td style="color: #6b7280; font-size: 13px; padding: 0 0 12px; width: 34%;">Project</td>
                        <td style="color: #111827; font-size: 14px; font-weight: 700; padding: 0 0 12px;">{project_display}</td>
                    </tr>
                    <tr>
                        <td style="color: #6b7280; font-size: 13px; padding: 0 0 12px;">Invited by</td>
                        <td style="color: #111827; font-size: 14px; font-weight: 700; padding: 0 0 12px;">{inviter_display}</td>
                    </tr>
                    <tr>
                        <td style="color: #6b7280; font-size: 13px; padding: 0;">Role</td>
                        <td style="color: #111827; font-size: 14px; font-weight: 700; padding: 0;">{role_label}</td>
                    </tr>
                </table>
            </div>

            <div style="text-align: center; margin: 0 0 32px;">
                <a href="{invite_url}" style="display: inline-block; background-color: #78a530; color: #ffffff; text-decoration: none; padding: 15px 34px; border-radius: 999px; font-weight: 700; font-size: 15px;">
                    Accept Invitation
                </a>
            </div>

            <p style="color: #6b7280; font-size: 13px; line-height: 1.7; margin: 0; padding-top: 24px; border-top: 1px solid #e5e7eb;">
                This invitation expires in 7 days. If you weren't expecting this email, you can safely ignore it.
            </p>

            <div style="margin-top: 18px; padding: 16px 18px; background-color: #f9fafb; border-radius: 14px;">
                <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px;">Button not working? Open this secure link:</p>
                <a href="{invite_url}" style="color: #1856dd; font-size: 12px; line-height: 1.6; word-break: break-all; text-decoration: underline;">{invite_url}</a>
            </div>

            <p style="color: #9ca3af; font-size: 12px; margin: 18px 0 0; text-align: center;">
                Sent by ProMarshal
            </p>
        </div>
    </body>
    </html>
    """

    text_content = f"""
ProMarshal

Project Invitation

You're invited to join a project.

{inviter_display} invited you to join {project_display} on ProMarshal as a {role_label}.

Project: {project_display}
Invited by: {inviter_display}
Role: {role_label}

Accept your invitation: {invite_url}

This invitation expires in 7 days.

If you weren't expecting this email, you can safely ignore it.

Sent by ProMarshal
"""

    return await _send_email(
        to_email=to_email,
        subject=f"You're invited to join {project_name} on ProMarshal",
        html_content=html_content,
        text_content=text_content,
    )


async def send_otp_email(to_email: str, otp_code: str) -> bool:
    """
    Send OTP verification code via the configured provider.
    Returns True if sent successfully, False otherwise.
    """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f5f6f8; margin: 0; padding: 40px 20px;">
        <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 20px; padding: 44px 40px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);">
            <div style="text-align: center; margin-bottom: 32px;">
                <p style="color: #111111; font-size: 28px; font-weight: 800; letter-spacing: -0.02em; margin: 0;">ProMarshal</p>
            </div>

            <p style="color: #6b7280; font-size: 12px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin: 0 0 12px;">Secure Sign-In</p>

            <h2 style="color: #111827; font-size: 28px; line-height: 1.2; font-weight: 800; margin: 0 0 16px;">
                Your login code
            </h2>

            <p style="color: #4b5563; font-size: 16px; line-height: 1.7; margin: 0 0 28px;">
                Use the verification code below to complete your sign-in to ProMarshal.
            </p>

            <div style="text-align: center; margin: 0 0 32px;">
                <div style="display: inline-block; min-width: 220px; background-color: #f8fafc; border: 1px solid #e5e7eb; border-radius: 16px; padding: 22px 28px;">
                    <p style="color: #6b7280; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin: 0 0 10px;">Verification Code</p>
                    <p style="color: #111827; font-size: 36px; font-weight: 800; letter-spacing: 8px; margin: 0; font-family: 'Courier New', monospace;">{otp_code}</p>
                </div>
            </div>

            <p style="color: #6b7280; font-size: 13px; line-height: 1.7; margin: 0; padding-top: 24px; border-top: 1px solid #e5e7eb;">
                This code expires in 10 minutes and can only be used once.
            </p>

            <div style="margin-top: 18px; padding: 16px 18px; background-color: #f9fafb; border-radius: 14px;">
                <p style="color: #6b7280; font-size: 12px; margin: 0;">
                    If you didn't request this code, you can safely ignore this email. Someone may have entered your email address by mistake.
                </p>
            </div>

            <p style="color: #9ca3af; font-size: 12px; margin: 18px 0 0; text-align: center;">
                Sent by ProMarshal
            </p>
        </div>
    </body>
    </html>
    """

    text_content = f"""
ProMarshal

Secure Sign-In

Your login code

Use the verification code below to complete your sign-in to ProMarshal.

Verification code: {otp_code}

This code expires in 10 minutes and can only be used once.

If you didn't request this code, you can safely ignore this email. Someone may have entered your email address by mistake.

Sent by ProMarshal
"""

    return await _send_email(
        to_email=to_email,
        subject="Your ProMarshal Login Code",
        html_content=html_content,
        text_content=text_content,
    )
