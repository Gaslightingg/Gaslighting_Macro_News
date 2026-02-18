from __future__ import annotations

import logging

from .bot_app import ALLOWED_UPDATES, build_application
from .config import load_config


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    application = build_application(config)
    application.run_polling(allowed_updates=list(ALLOWED_UPDATES))


if __name__ == "__main__":
    main()
