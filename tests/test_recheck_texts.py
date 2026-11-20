import unittest

from roadie import i18n
from roadie.phases import host


class RecheckTextsTest(unittest.TestCase):
    def test_backup_done_points_to_status_page(self):
        self.assertIn("status page", i18n.t("backup.done", file="x", mb=1))

    def test_tmdb_can_be_added_later(self):
        self.assertIn("later", i18n.t("q.tmdb.help"))

    def test_ssh_wait_is_short_and_calm(self):
        self.assertLessEqual(host.SSH_WAIT_SECONDS, 300)
        hint = i18n.t("ssh.check.ban_hint", user="streamadmin")
        self.assertNotIn("web console", hint)
        self.assertLess(len(hint), 160)


if __name__ == "__main__":
    unittest.main()
