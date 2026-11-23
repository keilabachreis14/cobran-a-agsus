import os
import tempfile
import unittest

import app as robo


class MessageRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = robo.DB_PATH
        robo.DB_PATH = os.path.join(self.tmp.name, "test.sqlite3")
        robo.app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)
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

    def test_rotation_is_global_per_stage(self):
        with robo.connect() as c:
            self.assertEqual(robo.next_template_number(c, "transporte"), 1)
            self.assertEqual(robo.next_template_number(c, "transporte"), 2)
            self.assertEqual(robo.next_template_number(c, "transporte"), 3)
            self.assertEqual(robo.next_template_number(c, "transporte"), 4)
            self.assertEqual(robo.next_template_number(c, "transporte"), 1)
            self.assertEqual(robo.next_template_number(c, "entregue"), 1)

    def test_prepare_rotates_models_between_contacts(self):
        with robo.connect() as c:
            for idx in range(4):
                c.execute("""INSERT INTO contacts
                    (municipality,state,server_name,phone,phone_validated,authorized)
                    VALUES('Cidade','PR',?,?,1,1)""",
                    (f"Servidor {idx}", f"55419999999{idx:02d}"))
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery)
                VALUES('NF-ROT','Cidade','PR','faturada','2026-11-20')""")
        self.assertEqual(robo.prepare(), 4)
        with robo.connect() as c:
            models = [r["template_number"] for r in c.execute(
                "SELECT template_number FROM queue ORDER BY id"
            )]
        self.assertEqual(models, [1, 2, 3, 4])

    def test_prepare_does_not_duplicate_existing_queue(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO contacts
                (municipality,state,server_name,phone,phone_validated,authorized)
                VALUES('Cidade','PR','Servidor A','5541999999999',1,1)""")
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status,expected_delivery)
                VALUES('NF-1','Cidade','PR','faturada','2026-11-20')""")
        self.assertEqual(robo.prepare(), 1)
        self.assertEqual(robo.prepare(), 1)
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


    def test_tracking_result_is_saved_and_delivery_updates_invoice(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status)
                VALUES('NF-T','Cidade','PR','em_transporte')""")
            invoice_id = c.execute("SELECT id FROM invoices WHERE nf='NF-T'").fetchone()["id"]
        response = robo.app.test_client().post(
            f"/tracking/{invoice_id}",
            data={"status":"entregue","last_event":"Entregue ao destinatário",
                  "eta":"","delivered_date":"2026-11-19"},
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        with robo.connect() as c:
            inv = c.execute("SELECT status,delivered_date FROM invoices WHERE id=?",(invoice_id,)).fetchone()
            history = c.execute("SELECT status,last_event,source FROM tracking_history WHERE invoice_id=?",(invoice_id,)).fetchone()
        self.assertEqual(inv["status"], "entregue")
        self.assertEqual(inv["delivered_date"], "2026-11-19")
        self.assertEqual(history["status"], "entregue")
        self.assertEqual(history["source"], "manual-portal-translovato")

    def test_tracking_cannot_mark_delivered_without_delivery_date(self):
        with robo.connect() as c:
            c.execute("""INSERT INTO invoices
                (nf,municipality,state,status)
                VALUES('NF-T2','Cidade','PR','em_transporte')""")
            invoice_id = c.execute("SELECT id FROM invoices WHERE nf='NF-T2'").fetchone()["id"]
        response = robo.app.test_client().post(
            f"/tracking/{invoice_id}",
            data={"status":"entregue","last_event":"Consta entregue no portal",
                  "eta":"","delivered_date":""},
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        with robo.connect() as c:
            inv = c.execute("SELECT status FROM invoices WHERE id=?",(invoice_id,)).fetchone()
            history_count = c.execute("SELECT COUNT(*) FROM tracking_history WHERE invoice_id=?",(invoice_id,)).fetchone()[0]
        self.assertEqual(inv["status"], "em_transporte")
        self.assertEqual(history_count, 0)


if __name__ == "__main__":
    unittest.main()
