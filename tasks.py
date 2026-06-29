import os
from pathlib import Path

from celery_app import celery_app
from dotenv import load_dotenv
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

load_dotenv(Path(__file__).parent / "twilio.env")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")


@celery_app.task(name="tasks.send_sms_batch")
def send_sms_batch(message: str, from_number: str, recipients: list[dict]) -> dict:
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    results = []
    for recipient in recipients:
        try:
            sms = client.messages.create(
                body=message,
                from_=from_number,
                to=recipient["phone"],
            )
            results.append({
                "name": recipient["name"],
                "phone": recipient["phone"],
                "status": "sent",
                "sid": sms.sid,
            })
        except TwilioRestException as err:
            results.append({
                "name": recipient["name"],
                "phone": recipient["phone"],
                "status": "failed",
                "error": str(err),
            })

    sent_count = sum(1 for r in results if r["status"] == "sent")
    return {"sentCount": sent_count, "total": len(results), "results": results}
