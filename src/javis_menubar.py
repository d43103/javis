"""Entry point for Javis macOS menu bar app.

Usage:
  python -m src.javis_menubar --hub ws://localhost:8766 --session voice-mac --auto-start
"""
import argparse
import logging


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Javis macOS menu bar voice agent")
    parser.add_argument("--hub", default="ws://localhost:8766", help="Mac Hub WebSocket URL")
    parser.add_argument("--session", default="voice-mac", help="Session ID")
    parser.add_argument("--auto-start", action="store_true", help="실행 즉시 bridge 시작")
    args = parser.parse_args()

    from src.menubar_app import JavisMenuBarApp

    app = JavisMenuBarApp(
        hub_url=args.hub,
        session_id=args.session,
        auto_start=args.auto_start,
    )
    app.run()


if __name__ == "__main__":
    main()
