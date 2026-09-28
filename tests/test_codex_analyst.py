"""Codex failure isolation, provenance, and credential boundary regressions."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import analyst_codex as C

class CodexTests(unittest.TestCase):
    def setUp(self):
        self.candidates = {'candidates': [{'url': 'https://example.com/story', 'title': 'CVE-2026-1234'}]}
        self.decision = {'decision': 'post', 'posts': [{
            'url': 'https://example.com/story', 'title': 'Update', 'score': 60,
            'post_text': 'Patch CVE-2026-1234.', 'comment': 'https://example.com/story',
            'confidence': 'high', 'cve_ids': ['CVE-2026-1234']}], 'alerts': [], 'considered': []}

    def test_failed_web_opens_do_not_count_as_verification(self):
        self.assertFalse(C.web_succeeded({'results': [{'title': 'Internal Error', 'snippet': 'Total lines: 1'}]}))
        self.assertFalse(C.web_succeeded({}))
        self.assertTrue(C.web_succeeded({'results': [{'title': 'Vendor advisory', 'snippet': 'Total lines: 80'}]}))

    def test_summary_only_is_held_and_low_confidence(self):
        d = C.checked_decision(json.dumps(self.decision), self.candidates, False)
        self.assertEqual(d['analyst'], 'codex-summary')
        self.assertEqual(d['posts'][0]['confidence'], 'low')

    def test_summary_hallucinated_cve_is_dropped(self):
        self.decision['posts'][0]['cve_ids'] = ['CVE-2026-9999']
        d = C.checked_decision(json.dumps(self.decision), self.candidates, False)
        self.assertEqual(d['posts'], [])
        self.assertEqual(d['decision'], 'skip')

    def test_unknown_alert_source_rejected(self):
        self.decision['alerts'] = [{'url': 'https://evil.test/invented'}]
        with self.assertRaisesRegex(ValueError, 'alert URL'):
            C.checked_decision(json.dumps(self.decision), self.candidates, True)

    def test_malformed_shape_rejected(self):
        for key in ['posts', 'alerts', 'considered']:
            d = dict(self.decision, **{key: 'bad'})
            with self.assertRaises(ValueError):
                C.checked_decision(json.dumps(d), self.candidates, True)

    def test_no_credentials_or_tools_and_requires_completed_turn(self):
        root = Path(tempfile.mkdtemp(prefix='cti-codex-test-'))
        (root / 'config').mkdir()
        (root / 'config/settings.yaml').write_text('analyst: {}\n')
        def fake(cmd, prompt, env, work, stdout, stderr, timeout):
            self.assertNotIn('OPENAI_API_KEY', env)
            self.assertNotIn('LINKEDIN_ACCESS_TOKEN', env)
            self.assertIn('forced_login_method="chatgpt"', cmd)
            self.assertIn('read-only', cmd)
            for f in C.DISABLED:
                self.assertIn(f, cmd)
            stdout.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'OK'}})+'\n')
            stdout.write(json.dumps({'type': 'turn.completed'})+'\n')
            return type('Result', (), {'returncode': 0})()
        with patch.object(C, 'ROOT', root), patch.dict(os.environ, {'OPENAI_API_KEY': 'test-secret', 'LINKEDIN_ACCESS_TOKEN': 'test-secret'}), patch.object(C, '_execute', fake):
            self.assertEqual(C.call_codex('test', web=False), ('OK', False))

if __name__ == '__main__':
    unittest.main()
