import os
import tempfile
import unittest

import app as robo


class MessageRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = robo.DB_PATH
        robo.DB_PATH = os.path.join(self.tmp.name, "test.sqlite3")
        robo.init_db()

    def tearDown(self):
        robo.DB_PATH = self.old_db
        self.tmp.cleanup()

    def add_contact(self, server):
        with robo.connect() as c:
            c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR',?,?,1,1)""",
                (server, "5541999999999"))

    def add_invoice(self, nf):
        with robo.connect() as c:
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery)
                VALUES(?,'Cidade','PR','faturada','2026-11-20')""", (nf,))

    def test_rotation_is_individual_per_contact_and_stage(self):
        with robo.connect() as c:
            cid = c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR','Servidor A','5541999999999',1,1)""").lastrowid
            self.assertEqual(robo.next_template_number(c, cid, "transporte"), 1)
            self.assertEqual(robo.next_template_number(c, cid, "transporte"), 2)
            self.assertEqual(robo.next_template_number(c, cid, "transporte"), 3)
            self.assertEqual(robo.next_template_number(c, cid, "transporte"), 4)
            self.assertEqual(robo.next_template_number(c, cid, "transporte"), 1)
            self.assertEqual(robo.next_template_number(c, cid, "entregue"), 1)

    def test_prepare_does_not_duplicate_existing_queue(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR','Servidor A','5541999999999',1,1)""")
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery)
                VALUES('NF-1','Cidade','PR','faturada','2026-11-20')""")
        self.assertEqual(robo.prepare(), 1)
        self.assertEqual(robo.prepare(), 0)
        with robo.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM queue").fetchone()[0], 1)

    def test_blocked_invoice_is_not_queued(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR','Servidor A','5541999999999',1,1)""")
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery,blocked)
                VALUES('NF-B','Cidade','PR','faturada','2026-11-20',1)""")
        self.assertEqual(robo.prepare(), 0)

    def test_unvalidated_or_unauthorized_contacts_are_excluded(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR','Servidor A','5541999999999',0,1)""")
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery)
                VALUES('NF-2','Cidade','PR','faturada','2026-11-20')""")
        self.assertEqual(robo.prepare(), 0)


if __name__ == "__main__":
    unittest.main()
