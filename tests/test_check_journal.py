import os, subprocess, tempfile, unittest
from pathlib import Path
S = Path(__file__).resolve().parents[1] / "interactive" / "check-journal.sh"
FAKE = r'''#!/bin/bash
echo "$@" >> "$R/log"
case "$*" in
 *"logs"*) [ -n "$LOGS_FAIL" ] && exit 1; [ -n "$LOGS_EMPTY" ] && exit 0; printf 'x\n'; for i in $(seq 1 ${INGEST_N:-0}); do echo "POST /ingest 200"; done;;
 *"exec"*) cat >/dev/null; printf '%s' "$LLM_OUT";;
esac
'''
CURL = r'''#!/bin/bash
echo "curl $@" >> "$R/log"
case "$*" in *audio=@*) printf '{"job_id":"j1","status":"queued"}';; *) printf '%s' "$ASR_OUT";; esac
'''
def run(llm="pong", asr='{"status":"complete","result":{"backend":"local","text":"And so, my fellow Americans, ask not what your country can do for you"}}', ingest="0", audio=True, extra=None):
    with tempfile.TemporaryDirectory() as t:
        r = Path(t); (r/"bin").mkdir(); (r/"log").write_text("")
        for n, c in (("docker", FAKE), ("curl", CURL)):
            (r/"bin"/n).write_text(c); (r/"bin"/n).chmod(0o755)
        (r/"a.wav").write_text("x")
        env = dict(os.environ, PATH=f"{r}/bin:"+os.environ["PATH"], R=str(r), LLM_OUT=llm, ASR_OUT=asr,
                   INGEST_N=ingest, JOURNAL_COMPOSE_FILE="c.yml", JOURNAL_POLL_SLEEP="0", **(extra or {}),
                   JOURNAL_ASR_AUDIO=str(r/"a.wav") if audio else str(r/"none.wav"))
        p = subprocess.run(["bash", str(S)], env=env, capture_output=True, text=True)
        return p, (r/"log").read_text()

class T(unittest.TestCase):
    def test_pass(self):
        p, log = run(); self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("/transcribe", log); self.assertNotIn("/ingest ", log.replace("POST /ingest", ""))
    def test_empty_llm(self):
        p, _ = run(llm="  \n"); self.assertEqual(p.returncode, 1); self.assertIn("empty LLM", p.stdout)
    def test_wrong_transcript(self):
        p, _ = run(asr='{"status":"complete","result":{"backend":"local","text":"hello"}}'); self.assertEqual(p.returncode, 1); self.assertIn("wrong transcript", p.stdout)
    def test_ingest_hit_detected(self):
        # the fake reports the same count before and after, so a hit is simulated via a growing counter
        with tempfile.TemporaryDirectory() as t:
            r = Path(t); (r/"bin").mkdir()
            (r/"bin"/"docker").write_text(FAKE.replace('${INGEST_N:-0}', '$(cat '+str(r)+'/n 2>/dev/null || echo 0)')
                .replace('printf \'x\\n\';', 'echo $(( $(cat '+str(r)+'/n 2>/dev/null || echo 0) + 1 )) > '+str(r)+'/n;'))
            (r/"bin"/"curl").write_text(CURL)
            for n in ("docker", "curl"): (r/"bin"/n).chmod(0o755)
            (r/"log").write_text(""); (r/"a.wav").write_text("x")
            env = dict(os.environ, PATH=f"{r}/bin:"+os.environ["PATH"], R=str(r), LLM_OUT="pong",
                       ASR_OUT='{"status":"complete","result":{"backend":"local","text":"ask not what your country can do for you"}}', JOURNAL_POLL_SLEEP="0", JOURNAL_COMPOSE_FILE="c.yml",
                       JOURNAL_ASR_AUDIO=str(r/"a.wav"))
            p = subprocess.run(["bash", str(S)], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 1); self.assertIn("ingest hit", p.stdout)
    def test_multipart_field_audio_and_job_poll(self):
        p, log = run(); self.assertIn("audio=@", log); self.assertNotIn("file=@", log)
        self.assertIn("/transcribe/j1", log)
    def test_hosted_backend_rejected(self):
        p, _ = run(asr='{"status":"complete","result":{"backend":"openai","text":"ask not what your country can do for you"}}')
        self.assertEqual(p.returncode, 1); self.assertIn("not local", p.stdout)
    def test_failed_job(self):
        p, _ = run(asr='{"status":"failed","error":"x"}'); self.assertEqual(p.returncode, 1)
    def test_logs_unreadable_fails_closed(self):
        p, _ = run(extra={"LOGS_FAIL": "1"}); self.assertEqual(p.returncode, 1); self.assertIn("unreadable or empty", p.stdout)
    def test_logs_empty_fails_closed(self):
        p, _ = run(extra={"LOGS_EMPTY": "1"}); self.assertEqual(p.returncode, 1); self.assertIn("unreadable or empty", p.stdout)
    def test_missing_audio(self):
        self.assertEqual(run(audio=False)[0].returncode, 1)
if __name__ == "__main__": unittest.main()
