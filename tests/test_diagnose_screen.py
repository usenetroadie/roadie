import unittest

from roadie.ops import diagnose

REPORT = """### roadie report
version: 0.5.1
finished steps: preflight, packages

### services
aiostreams: running healthy (img)

### watcher
no problems

### last log lines: aiometadata-redis
1:M 19 Nov 2026 WARNING Redis is running in protected mode
1:M 19 Nov 2026 Ready to accept connections

### last log lines: aiostreams
2026-11-19 INFO started
2026-11-19 WARN Rate Limit Exceeded for 10.0.0.1, try later
2026-11-19 ERROR Failed to fetch https://indexer: timeout

### maintenance jobs
done
"""


class ScreenTest(unittest.TestCase):
    def test_summary_on_top(self):
        line = diagnose.summary_line([{"Service": "a", "State": "running", "Health": "healthy"},
                                      {"Service": "b", "State": "exited", "Health": ""}],
                                     {"container:b": {}}, {"step": "proof", "at": "x", "message": "m"})
        self.assertIn("1 of 2", line)
        self.assertIn("proof", line)

    def test_screen_filters_harmless(self):
        text = diagnose.screen_text(REPORT)
        self.assertNotIn("protected mode", text)
        self.assertNotIn("Rate Limit Exceeded", text)
        self.assertNotIn("Ready to accept", text)

    def test_screen_keeps_real_errors(self):
        text = diagnose.screen_text(REPORT)
        self.assertIn("ERROR Failed to fetch", text)
        self.assertIn("version: 0.5.1", text)
        self.assertIn("aiostreams: running healthy", text)

    def test_report_file_complete(self):
        self.assertIn("protected mode", REPORT)  # the file gets build() unchanged
        self.assertLess(len(diagnose.screen_text(REPORT)), len(REPORT))


if __name__ == "__main__":
    unittest.main()
