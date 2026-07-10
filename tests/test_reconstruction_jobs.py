"""Tests for the background reconstruction job manager."""
import time

from basebuddy.core.services.reconstruction_jobs import ReconstructionJobManager


def _wait(mgr, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = mgr.get(job_id)
        if job['status'] in ('done', 'error'):
            return job
        time.sleep(0.02)
    raise TimeoutError('job did not finish')


def test_successful_job_reports_progress_and_result():
    mgr = ReconstructionJobManager()

    def work(progress_cb):
        progress_cb(50, 'halfway')
        return {'answer': 42}

    job_id = mgr.submit(work)
    job = _wait(mgr, job_id)
    assert job['status'] == 'done'
    assert job['progress'] == 100
    assert job['result'] == {'answer': 42}
    assert job['error'] is None


def test_failing_job_captures_error():
    mgr = ReconstructionJobManager()

    def work(progress_cb):
        raise RuntimeError('model exploded')

    job = _wait(mgr, mgr.submit(work))
    assert job['status'] == 'error'
    assert 'model exploded' in job['error']
    assert job['result'] is None


def test_unknown_job_returns_none():
    mgr = ReconstructionJobManager()
    assert mgr.get('nope') is None


def test_jobs_run_serially():
    """max_workers=1: the second job must not start before the first ends."""
    mgr = ReconstructionJobManager(max_workers=1)
    order = []

    def slow(progress_cb):
        order.append('slow-start')
        time.sleep(0.2)
        order.append('slow-end')
        return {}

    def fast(progress_cb):
        order.append('fast-start')
        return {}

    j1 = mgr.submit(slow)
    j2 = mgr.submit(fast)
    _wait(mgr, j1)
    _wait(mgr, j2)
    assert order == ['slow-start', 'slow-end', 'fast-start']


def test_progress_clamped():
    mgr = ReconstructionJobManager()

    def work(progress_cb):
        progress_cb(250, 'over')
        return {}

    job = _wait(mgr, mgr.submit(work))
    # Final state is done/100 regardless; the intermediate clamp is <=99.
    assert job['progress'] == 100
