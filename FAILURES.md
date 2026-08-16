# Known failure modes

Based on actual testing (unit tests exercising the dedup race, the worker retry
loop, and the reconciler — see `tests/`), plus reasoning about the parts I
couldn't fully load-test against the real mock API before submitting.

- **`comment.deleted` arriving before the matching `comment.created`.** The
  spec says arrival order isn't guaranteed. If a delete event arrives first,
  there's no `dm_attempts` row yet to cancel — `cancel_pending_by_comment`
  finds nothing and the later `comment.created` will still queue and send a
  DM for a comment that's already gone. I didn't build a "seen deletions"
  table to catch this because it adds a second piece of state to keep
  consistent for a genuinely rare ordering, but it is a real gap, not a
  theoretical one — `sent_at` mismatched arrival order is called out
  explicitly in the spec as something that happens.

- **`in_flight` DMs that fail between polling intervals aren't caught until
  the next reconciler pass.** The reconciler runs every 10s and only looks at
  rows that have been `in_flight` for 30+ seconds. A DM that gets accepted,
  fails within that window, and the process is killed before the next pass
  runs, sits in `in_flight` — correctly reported as `queued`, not `failed` —
  until the process restarts and the reconciler catches up. Numbers stay
  honest, but there's a real window (up to ~40s) where a failed send hasn't
  been detected as failed yet.

- **The in-memory rate limiter resets on restart.** If the process restarts
  mid-burst, the worker has no memory of the last 60s of sends and can fire a
  new batch immediately, likely drawing one or two avoidable `429`s before it
  self-corrects. This was a deliberate tradeoff (see the prompt/design doc)
  — the `429` path already retries safely, so this never loses or duplicates
  a DM, it just costs a few wasted round trips right after a restart.

- **A single sqlite3 connection guarded by one `asyncio.Lock` serializes every
  read and write, including `/stats` reads.** Under the 500-events/10s load
  test this held up fine because each query is sub-millisecond, but it's a
  single point of write contention — `/webhook`, the worker loop, and the
  reconciler are all competing for the same lock. At meaningfully higher
  throughput than this assignment's target, this would need to move off a
  single blocking connection (`aiosqlite`, or Postgres) rather than continuing
  to scale the lock.

**Fixed during testing, not shipped as a bug:** the worker's polling query
originally selected both `pending` and `in_flight` rows as "due to send,"
which meant an already-accepted (`202`) DM kept getting re-sent by the worker
every poll cycle instead of being left for the reconciler to check. Caught by
a unit test that mocked the send call and asserted call counts — a
successful send was being called 5 times instead of 1. Fixed by scoping the
worker's query to `status = 'pending'` only; `in_flight` rows are now only
ever touched by the reconciler. Flagging this here because it's exactly the
kind of duplicate-send bug this assignment is testing for, and it's better
that you hear about it from me than find it independently.
