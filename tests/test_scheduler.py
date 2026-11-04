import unittest

from gpu_manager.scheduler import View, decide
from gpu_manager.store import Job

TOTAL = 32624
PAIR = 30000
MARGIN = 1024


def job(id, vram, status="queued", submitted=0.0, started=None):
    return Job(id=id, name=f"j{id}", owner=None, vram_mib=vram, argv=["x"], cwd="/",
               retries=2, grace_seconds=60, status=status, submitted_at=submitted,
               started_at=started, ended_at=None, failures=0, interruptions=0,
               exit_code=None, stop_request=None, chunk=None, chunks=None, pct=None,
               progress_at=None, note=None)


def view(queued=(), active=(), pair="up", idle_min=0.0, used=None, **kw):
    if used is None:
        used = (29391 if pair in ("up", "starting", "stopping") else 0) + sum(j.vram_mib for j in active)
    return View(now=10_000.0, queued=list(queued), active=list(active), pair=pair,
                last_interactive_at=10_000.0 - idle_min * 60, vram_total_mib=TOTAL,
                vram_used_mib=used, **kw)


def run(v, preempt=False):
    return decide(v, MARGIN, PAIR, 30, preempt)


class TestScheduler(unittest.TestCase):
    def test_idle_card_with_empty_queue_brings_pair_up(self):
        self.assertEqual(run(view(pair="down")), [("start_pair",)])

    def test_nothing_to_do_when_pair_up_and_queue_empty(self):
        self.assertEqual(run(view()), [])

    def test_big_job_waits_out_the_hold(self):
        self.assertEqual(run(view(queued=[job(1, 18000)], idle_min=10)), [])

    def test_big_job_takes_card_after_hold(self):
        self.assertEqual(run(view(queued=[job(1, 18000)], idle_min=31)), [("stop_pair",)])

    def test_job_starts_once_pair_is_down(self):
        self.assertEqual(run(view(queued=[job(1, 18000)], pair="down")), [("start_job", 1)])

    def test_job_waits_while_pair_stopping(self):
        self.assertEqual(run(view(queued=[job(1, 18000)], pair="stopping", idle_min=60)), [])

    def test_small_job_runs_beside_pair(self):
        # 32624 - 1024 - 30000 = 1600 MiB of room beside the pair
        self.assertEqual(run(view(queued=[job(1, 1500)])), [("start_job", 1)])

    def test_measured_usage_blocks_even_if_declared_fits(self):
        self.assertEqual(run(view(queued=[job(1, 1500)], used=31000)), [])

    def test_fifo_two_jobs_fill_card(self):
        acts = run(view(queued=[job(1, 15000), job(2, 15000), job(3, 4000)], pair="down"))
        self.assertEqual(acts, [("start_job", 1), ("start_job", 2)])

    def test_backfill_cannot_starve_head(self):
        # Head (#1, 31000) waits for the hold; #2 fits beside the pair but would
        # leave the head no room once the pair goes, so it does not jump ahead.
        acts = run(view(queued=[job(1, 31000), job(2, 1500)], idle_min=5))
        self.assertEqual(acts, [])

    def test_backfill_allowed_when_head_still_fits(self):
        acts = run(view(queued=[job(1, 20000), job(2, 1500)], idle_min=5))
        self.assertEqual(acts, [("start_job", 2)])

    def test_pair_stays_down_while_job_waits_on_running_batch(self):
        running = job(9, 20000, status="running", started=1.0)
        acts = run(view(queued=[job(1, 20000)], active=[running], pair="down"))
        self.assertEqual(acts, [])

    def test_pair_returns_when_queue_drains_and_room(self):
        running = job(9, 1000, status="running", started=1.0)
        self.assertEqual(run(view(active=[running], pair="down")), [("start_pair",)])

    def test_pair_stays_down_beside_big_running_job(self):
        running = job(9, 18000, status="running", started=1.0)
        self.assertEqual(run(view(active=[running], pair="down")), [])

    def test_interactive_demand_without_preemption_changes_nothing(self):
        running = job(9, 18000, status="running", started=1.0)
        acts = run(view(active=[running], pair="down", demand_pending=True))
        self.assertEqual(acts, [])

    def test_interactive_demand_with_preemption_evicts_newest_first(self):
        old = job(8, 10000, status="running", started=1.0)
        new = job(9, 10000, status="running", started=2.0)
        acts = run(view(active=[old, new], pair="down", demand_pending=True), preempt=True)
        self.assertEqual(acts, [("stop_job", 9, "requeue"), ("stop_job", 8, "requeue")])

    def test_take_stops_everything(self):
        running = job(9, 1000, status="running", started=1.0)
        acts = run(view(queued=[job(1, 500)], active=[running], take=True))
        self.assertEqual(acts, [("stop_job", 9, "requeue"), ("stop_pair",)])

    def test_unreadable_card_starts_nothing(self):
        v = view(queued=[job(1, 500)], pair="down")
        v.vram_used_mib = None
        self.assertEqual(run(v), [])


if __name__ == "__main__":
    unittest.main()
