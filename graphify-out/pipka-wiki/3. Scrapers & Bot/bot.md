# Bot Domain

Handles all interaction directly generated or ingested by Telegram Users (`python-telegram-bot` standard architecture).
Dependencies: [[services.md]], [[models.md]]

## Root Setup
Path: `app/bot/bot.py`
Maps dispatcher modules tying message updates onto structured python functions.

## Telegram Keys
Path: `app/bot/keyboards.py`
Provides pre-designed Inline Keyboard structures representing dynamic navigation maps:
1. Search Selection Layouts
2. Inbox Processing Layouts (Top, General, Complete)
3. Application Tracking Commands

## Context Handlers
Path: `app/bot/handlers/*.py`
Listens for user prompts modifying SQL instances defined in [[db.md]]:
- `inbox.py`: Traverses generic unreviewed aggregations.
- `search.py`: Intercepts `Search by My Profile` passing constraints straight down to [[sources.md]].
- `tracker.py`: Tracks standard applications tracking mutations inside the Tracker business scope.
