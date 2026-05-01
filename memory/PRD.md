# SCALE India Investment — PRD

## Original Problem Statement
Full-stack Indian Stock Market Paper Trading Simulator ("SCALE India Investment") with React + FastAPI (ported from Node spec at user's request) + MongoDB. Live NSE prices via session-based scraping with Yahoo Finance fallback, WebSocket streaming, JWT auth + bcrypt, dark Bloomberg-style terminal UI. ₹1,00,000 starting capital. ~80 NSE stocks. Sector filters, sparklines, sector donut, leaderboard with badges, reset portfolio, market-status IST awareness.

## Architecture
- **Backend**: FastAPI on :8001 behind /api ingress prefix; WebSocket at `/api/ws?token=<jwt>`
- **Frontend**: React (CRA + craco) at :3000, REACT_APP_BACKEND_URL via Kubernetes ingress
- **DB**: Local MongoDB via MONGO_URL (collections: users, portfolios, holdings, transactions)
- **Live data**: NSE quote-equity (session cookies, batched 5/group, 1.2s gap, 60s cycle) → Yahoo Finance fallback per-symbol on failure
- **Charts**: Yahoo Finance v8/finance/chart for OHLC candlesticks (1d/5d/1mo/3mo/6mo/1y)
- **Realtime**: in-process asyncio queue → WebSocket fan-out
- **Email**: Resend (transactional) for admin alerts and applicant notifications

## User Personas
- **Trader (approved user)** — log in, watch live prices, buy/sell paper shares, view candles
- **Pending applicant** — submitted signup application, waiting for creator approval
- **Creator/Admin** (`admin`/`admin123`) — sees Admin tab, approves/rejects/revokes applications, receives email alerts

### Iteration 5 — Trade-block + Reset purge + Admin Leaderboard control (2026-05-01)
- **Backend `/api/trade`** now hard-rejects with HTTP 400 ("Trading is disabled. NSE is currently closed.") when `fetcher.cache.market_status.status != "OPEN"` — covers holidays + outside hours.
- **Removed `/api/portfolio/reset` endpoint** entirely. ResetCount field on portfolios is now unused (kept for backward compat, not written).
- **Removed Reset Portfolio button + modal** from `PortfolioTab.jsx`.
- **MarketTab "Updated" column** now renders `Last traded <DD MMM>` (parsed from `marketStatus.tradeDate`) on closed days instead of "Prev close".
- **Buy/Sell buttons disabled** on MarketTab, PortfolioTab (holding-sell), and ChartModal when `marketStatus.sessionType === "CLOSED"`. TradeModal also defends with `invalid` flag and shows `warn-market-closed` banner.
- **Admin Leaderboard visibility toggle** (P1):
  - New User field `leaderboardHidden: bool` (default false).
  - New endpoint `POST /api/admin/leaderboard-visibility` body `{userId, hidden}` (admin-only).
  - `_user_app_view` now exposes `leaderboardHidden`.
  - `/api/leaderboard` excludes hidden users + admin accounts from `rows` for OTHER viewers; the user themselves still sees their own `me` row.
  - `AdminTab.jsx` adds a **Visible/Hidden** toggle column per row (data-testid `admin-leaderboard-toggle-<username>`) — green ✓ Visible / red ✕ Hidden.
- **49/53 pytest pass** (4 pre-existing flakes: 3× signup rate-limit, 1× Yahoo intraday). 11/11 NEW iter-5 tests green.

## Implemented (2026-04-30)
### Iteration 1 — MVP
- Auth, NSE+Yahoo fetcher, BUY/SELL trades, transactions, leaderboard with rank+badges+15 fakes, WebSocket, all 4 frontend tabs, TradeModal, Sparkline, SectorDonut. **19/19 backend pass**.

### Iteration 2 — Crimson theme + Approval gating
- Logo image (user-provided) on AuthPage and Navbar; theme `--blue` swapped to `#a01e20`
- Pending/approved/rejected user gating with 10-day approval window; existing users grandfathered
- Reapply flow; Admin tab with Pending/Approved/Rejected sub-tabs and Approve/Reject/Revoke
- **26/26 backend pass**.

### Iteration 3 — Charts + Email + Change Password
- **Candlestick Chart Modal**: opens by clicking a stock symbol/name in MarketTab. Range toggle 1D/5D/1M/3M/6M/1Y. Real OHLCV from Yahoo Finance. Volume bars + previous-close reference line + 52-week stats + inline Buy/Sell.
- **Resend email integration**:
  - Admin alert when a new application is submitted (to ADMIN_EMAIL)
  - Applicant notified on approve / reject (to applicant.email)
  - Branded HTML templates matching crimson terminal aesthetic
  - Fire-and-forget pattern; failures logged as WARNING (don't block API responses)
  - **Verified working**: log line `Email sent to=scalesupportteam2@gmail.com id=883b6567...`
  - **Resend test-mode caveat**: only delivers to verified address. To deliver to applicants, verify a domain at https://resend.com/domains and update SENDER_EMAIL.
- **Change Password**: ⚙ button in Navbar opens ChangePasswordModal. Validates min 6 chars, must differ from current, must match confirm. Inline error rendering for client-side validation + server errors (with toast).
- **38/38 backend pass**, all frontend flows verified.

## Test Credentials (`/app/memory/test_credentials.md`)
- Admin: `admin` / `admin123`
- Grandfathered user: `demo_user` / `demo123`

## Production Setup Notes
- **To deliver applicant emails outside Resend test mode**: verify a domain at resend.com/domains, then set `SENDER_EMAIL` in `/app/backend/.env` to use that domain (e.g., `noreply@yourdomain.com`)
- **JWT_SECRET** is a long random hex; rotate before production
- **Admin password** can be changed via the ⚙ button in Navbar (after first login)

## P0 / P1 / P2 Backlog
### P0
- Domain verification at Resend so all applicants receive email decisions
- Watchlist (star a stock) + price alerts (threshold-based notifications)

### P1
- Mobile responsive optimization for navbar metric pills
- Public read-only leaderboard URL (no login)
- Export transaction history to CSV
- "Streak" leaderboard (consecutive profitable days) for engagement
- Order book / depth display (already returned by NSE marketDeptOrderBook)

### P2
- Light theme variant
- Daily portfolio snapshot persistence for return calculation independent of starting cash
- Multi-broker simulation (separate portfolios per "account")

## Known Minor Issues
- Recharts ResponsiveContainer transient `width(-1) height(-1)` console warnings on initial render (cosmetic only)
- Rate limiter keys on `request.client.host`; behind shared-IP proxy multiple users share the bucket
