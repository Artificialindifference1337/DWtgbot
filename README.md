## Market volatility

Prices update every two minutes using weighted random percentage changes:

- small moves around 10% occur most often;
- moves around 100% occur regularly;
- moves around 500% occur occasionally;
- a +2,000% market event occurs roughly once per hour across the whole market.

Downward moves are capped at 95% so a product price can never become zero or negative.

# Drug Wars Telegram Bot

An asynchronous Telegram economy game built with Python, aiogram, and SQLAlchemy.

## Quick start

1. Copy `.env.example` to `.env`.
2. Add your `BOT_TOKEN`.
3. Add your Telegram numeric user ID to `ADMIN_USER_IDS` to enable `/admin`.
4. Install dependencies: `python -m pip install -r requirements.txt`.
5. Initialize or upgrade the database: `python -m app.init_db`.
6. Start the bot: `python -m app.main`.

Example:

```env
BOT_TOKEN=123456789:your-token
DATABASE_URL=sqlite+aiosqlite:///./drugwars.db
GAME_TIMEZONE=Europe/Amsterdam
ADMIN_USER_IDS=123456789
LOG_LEVEL=INFO
```

Multiple admins can be entered as comma-separated IDs:

```env
ADMIN_USER_IDS=123456789,987654321
```

## Current version

- English user interface
- Prices refresh every two minutes, aligned to even clock minutes
- Live price countdown; no refresh button required
- Buy menu displays current prices
- Price page displays current and last price
- Raid cooldown is 30 seconds
- Live raid cooldown; targets appear automatically when it reaches zero
- Player playtime page with daily and total time
- Admin `/admin` playtime reports:
  - minutes per day per player
  - total minutes of all players per day
  - total minutes per player
  - total minutes across all players
- Existing players, balances, inventory, loans, and market data are retained

## How playtime is measured

A player becomes active when they interact with the bot. Time is counted until two minutes after their most recent interaction. An inactive gap between separate sessions is not counted. Telegram does not expose whether somebody is merely looking at a bot chat, so interaction-based session tracking is the reliable available measurement.

## Existing installation upgrade

Stop the old bot and copy the existing `.env` and `drugwars.db` into this version. Then run:

```bash
python -m app.init_db
python -m app.main
```

`init_db` creates the new playtime tables without deleting existing game data.

## Tests

```bash
pytest -q
```

## Balanced market model

Prices update every two minutes using a mean-reverting market model:

- Most ticks move up to roughly 8%.
- Active ticks can move up to roughly 22%.
- Occasional turbulent ticks can move up to roughly 55%.
- About once per hour, one product receives a temporary 3x-12x event price.
- Every product remains between 20% and 20x its configured base price.
- Event prices are calculated from the base price rather than compounded from the previous event.

Existing prices below the new floor recover automatically on the next market update.

## Advanced market

The compact market screen updates live and links to a detailed page for each product. The market now includes persistent bull, bear, sideways and volatile regimes, momentum, mean reversion, rare news-driven spikes, 24-hour high/low values, trading volume, a sparkline and a simple buy/hold/sell signal.

Run `python -m app.init_db` after upgrading. This creates the new market state, history, news and trade-record tables without deleting existing player data.


## Token economy and custom selling

Players earn keys from daily logins, seven-day streaks, level-ups, a 1% trade drop chance and a 10% successful-raid drop chance. The Black Market sells boosts, raid resets, cash crates and permanent warehouse expansions. Every key mutation is recorded in `token_ledger`.

The Sell menu displays the player's units for every product. Players can use fixed quantities, Sell all, or type a custom whole-number quantity. Inventory is validated again when the sale is executed.

## Playtime tracking fix

Playtime middleware is registered on Aiogram's message and callback-query observers. A player is considered active from an interaction until the configured idle timeout. The scheduler credits active seconds to `daily_playtime`.

## GitHub / secrets

Never commit `.env`, `drugwars.db`, PostgreSQL data, or your real Telegram bot token. The included `.gitignore` excludes these files. Keep `.env.example` limited to placeholder values. If a bot token is ever committed publicly, revoke it with BotFather and create a new token.
