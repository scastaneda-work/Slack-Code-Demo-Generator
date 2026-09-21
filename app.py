import asyncio
import logging
import os

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from agent.agent import log_model_check
from listeners import register_listeners

load_dotenv(dotenv_path=".env", override=False)

logging.basicConfig(level=logging.DEBUG)

app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    client=AsyncWebClient(
        base_url=os.environ.get("SLACK_API_URL", "https://slack.com/api"),
        token=os.environ.get("SLACK_BOT_TOKEN"),
    ),
)

register_listeners(app)


async def main():
    # Startup self-check: log the configured model + what the gateway actually
    # serves, so a 401 / silent reroute is visible in the init log (never fatal).
    await log_model_check()
    handler = AsyncSocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
