# Failure Modes & Edge Case Analysis

This document details the exact conditions under which this Instagram automation system can fail, double-send, lose a DM, or misreport a statistic.

---

## 1. Process Crash Between Downstream `202` Response and Local DB Commit

- **Exact Condition**: The worker calls `POST /v1/dm/send`. The downstream API accepts the request and returns `200/202` with `{"dm_id": "dm_123"}`. Before the background worker can commit `status = 'in_flight'` and `dm_id = 'dm_123'` to the local SQLite database, the process receives `SIGKILL` or power is cut.
- **System Behavior**: Upon restart, the database row is still in `status = 'pending'`. The background worker picks up the row and re-executes `POST /v1/dm/send`.
- **Mitigation & Risk**: Because the request includes a deterministic `Idempotency-Key: f"{user_id}:{rule_id}"`, the downstream API recognizes the duplicate request and returns the original `dm_id` without dispatching a second DM.
- **Residual Risk**: If the downstream API's idempotency key cache expires before the backend process recovers, a duplicate DM will be sent.

---

## 2. `comment.deleted` Arriving After DM Dispatched (`in_flight` or `delivered`)

- **Exact Condition**: A user posts a keyword comment. The webhook processes `comment.created` and the worker immediately dispatches `POST /v1/dm/send`, receiving `202` (`status = 'in_flight'`). 5 seconds later, the user deletes their comment, sending a `comment.deleted` webhook event.
- **System Behavior**: The handler queries `dm_attempts` by `comment_id`. Because the row status is `in_flight` (not `pending`), the system leaves it untouched.
- **Why**: Instagram DMs cannot be unsent once accepted by the outbound provider. Attempting to recall or cancel an in-flight message is impossible at the protocol level.
- **Stat Impact**: The DM will be reported under `sent` (if delivered) or `failed` (if delivery fails), rather than `cancelled`.

---

## 3. Persistent Downstream Outage Exceeding Retry Cap

- **Exact Condition**: The downstream DM API returns `500 Internal Server Error` or encounters network timeouts continuously for 5 consecutive attempts.
- **System Behavior**: Exponential backoff schedules retries at 2s, 4s, 8s, 16s, and 32s. Upon the 5th failure, the row transitions permanently to `status = 'failed'`.
- **System Limitation**: If the downstream API recovers on attempt #6 (e.g. 10 minutes later), the system will not attempt another send.
- **Stat Impact**: Accurately reported as `failed`.

---

## 4. In-Memory Rate Limiter Reset on Server Restart

- **Exact Condition**: The server is handling heavy outbound traffic near the 10 req / 60s limit and is abruptly restarted.
- **System Behavior**: The in-memory sliding window timestamps are cleared. Upon startup, the worker may dispatch up to 10 requests immediately.
- **Mitigation & Risk**: If downstream rate limits are exceeded, the API returns `429` with a `Retry-After` header. The worker catches `429`, parses `Retry-After`, and reschedules `next_attempt_at` safely without losing data or failing the send.

---

## 5. Webhook Delivery Failure Prior to Backend Ingestion

- **Exact Condition**: The upstream comment-event producer experiences a network failure or DNS resolution error before the HTTP request reaches `POST /webhook`.
- **System Behavior**: The event never reaches the service and is not recorded in SQLite.
- **Stat Impact**: The event is uncounted. `/stats` reflects server-side truth of ingested events only.
