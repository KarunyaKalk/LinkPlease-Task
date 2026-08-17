# Loom Video Talking Points: "What I'd Change With One More Week"

Here is a concise breakdown for your video recording:

---

### 1. Horizontal Scaling & Distributed Outbox
- **Current State**: Single-process SQLite queue in WAL mode with local thread locks. Perfect for a single-instance service.
- **With 1 More Week**: Transition to PostgreSQL with a Transactional Outbox pattern (`LISTEN / NOTIFY` or SELECT FOR UPDATE SKIP LOCKED) and Redis-backed distributed locks (`Redlock`). This allows scaling worker instances horizontally behind a load balancer without race conditions.

---

### 2. Distributed Rate Limiting
- **Current State**: In-memory sliding window (10 req / 60s). Losing window state on restart is safe because retry backoff handles `429`s.
- **With 1 More Week**: Move the sliding window rate limiter into Redis using a sorted set (`ZADD` / `ZREMRANGEBYSCORE`). This enforces global rate limits across multiple container instances sharing the outbound API key.

---

### 3. Jittered Backoff & Dead Letter Queue (DLQ)
- **Current State**: Strict exponential backoff capped at 5 attempts (`2^n` seconds).
- **With 1 More Week**: Add full randomized jitter (`random.uniform(0, 2^n)`) to eliminate thundering herd spikes when downstream recovers from an outage. Add an administrative DLQ endpoint (`POST /attempts/{id}/retry`) to manually re-queue failed messages after resolving upstream issues.

---

### 4. Comprehensive Observability & Tracing
- **Current State**: In-database status stats (`/stats`).
- **With 1 More Week**: Add OpenTelemetry tracing and Prometheus metrics (`/metrics`) tracking webhook processing duration, reconciliation lag, worker queue depth, and downstream status code distributions.
