import csv, io, os, re, sqlite3
from datetime import date, datetime
from urllib.parse import quote
from flask import Flask, flash, redirect, render_template_string, request, url_for
from modules.messages import TRANSPORT_MESSAGES, DELIVERED_MESSAGES

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
DB_PATH = os.path.join(DATA, "robo_cobrancas.sqlite3")
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-local-secret")
TRANSLOVATO_CNPJ = digits(os.environ.get("TRANSLOVATO_CNPJ", "07626776000160"))
TRANSLOVATO_TRACKING_URL = "https://www.translovato.com.br/minha-carga/"

def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contacts(
          id INTEGER PRIMARY KEY, municipality TEXT NOT NULL, state TEXT NOT NULL,
          server_name TEXT NOT NULL, phone TEXT NOT NULL,
          phone_validated INTEGER NOT NULL DEFAULT 0, authorized INTEGER NOT NULL DEFAULT 0,
          UNIQUE(municipality,state,server_name,phone));
        CREATE TABLE IF NOT EXISTS invoices(
          id INTEGER PRIMARY KEY, nf TEXT UNIQUE NOT NULL, municipality TEXT NOT NULL,
          state TEXT NOT NULL, carrier TEXT DEFAULT '', status TEXT DEFAULT 'faturada',
          expected_delivery TEXT DEFAULT '', delivered_date TEXT DEFAULT '',
          blocked INTEGER DEFAULT 0, signed_invoice_received INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS queue(
          id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL, contact_id INTEGER NOT NULL,
          stage TEXT NOT NULL, template_number INTEGER NOT NULL, body TEXT NOT NULL,
          created_at TEXT NOT NULL, sent_confirmed_at TEXT DEFAULT '',
          UNIQUE(invoice_id,contact_id,stage),
          FOREIGN KEY(invoice_id) REFERENCES invoices(id),
          FOREIGN KEY(contact_id) REFERENCES contacts(id));
        CREATE TABLE IF NOT EXISTS message_counters(
          contact_id INTEGER NOT NULL, stage TEXT NOT NULL,
          next_number INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(contact_id,stage),
          FOREIGN KEY(contact_id) REFERENCES contacts(id));
        CREATE TABLE IF NOT EXISTS tracking_history(
          id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL,
          checked_at TEXT NOT NULL, status TEXT NOT NULL,
          last_event TEXT DEFAULT '', eta TEXT DEFAULT '',
          source TEXT NOT NULL DEFAULT 'manual',
          FOREIGN KEY(invoice_id) REFERENCES invoices(id));
        """)

def digits(s): return re.sub(r"\D", "", s or "")
def valid_phone(p): return p.startswith("55") and len(p) in (12,13)

def read_csv(file):
    raw = file.read().decode("utf-8-sig")
    return csv.DictReader(io.StringIO(raw), delimiter=";" if raw.count(";") > raw.count(",") else ",")

def next_template_number(c, contact_id, stage):
    row = c.execute(
        "SELECT next_number FROM message_counters WHERE contact_id=? AND stage=?",
        (contact_id, stage)
    ).fetchone()
    number = row["next_number"] if row else 1
    following = number % 4 + 1
    c.execute("""INSERT INTO message_counters(contact_id,stage,next_number)
        VALUES(?,?,?) ON CONFLICT(contact_id,stage)
        DO UPDATE SET next_number=excluded.next_number""",
        (contact_id, stage, following))
    return number

def render_body(stage, number, inv):
    data = dict(nf=inv["nf"], municipio=f'{inv["municipality"]}/{inv["state"]}',
                transportadora=inv["carrier"] or "A confirmar",
                previsao=inv["expected_delivery"] or "A confirmar",
                data_entrega=inv["delivered_date"] or "A confirmar")
    return (TRANSPORT_MESSAGES if stage == "transporte" else DELIVERED_MESSAGES)[number-1].format(**data)

def prepare():
    total=0
    with connect() as c:
        invoices=c.execute("SELECT * FROM invoices WHERE blocked=0 AND signed_invoice_received=0").fetchall()
        for inv in invoices:
            if inv["status"]=="entregue" and inv["delivered_date"]:
                stage="entregue"
                try: date.fromisoformat(inv["delivered_date"])
                except ValueError: continue
            elif inv["status"] in ("faturada","em_transporte") and inv["expected_delivery"]:
                stage="transporte"
            else: continue
            contacts=c.execute("""SELECT * FROM contacts WHERE municipality=? AND state=?
                AND phone_validated=1 AND authorized=1""",(inv["municipality"],inv["state"])).fetchall()
            for contact in contacts:
                exists=c.execute("SELECT 1 FROM queue WHERE invoice_id=? AND contact_id=? AND stage=?",
                                 (inv["id"],contact["id"],stage)).fetchone()
                if exists: continue
                n=next_template_number(c, contact["id"], stage)
                body=render_body(stage,n,inv)
                c.execute("""INSERT INTO queue(invoice_id,contact_id,stage,template_number,body,created_at)
                    VALUES(?,?,?,?,?,?)""",(inv["id"],contact["id"],stage,n,body,datetime.now().isoformat(timespec="seconds")))
                total+=1
    return total

HTML = r"""
<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robô de Cobranças COMBO UBS</title><style>
*{box-sizing:border-box}body{font-family:Arial;background:#f2f5fa;color:#24344d;margin:0}
header{background:#203657;color:white;padding:22px 4%}main{max-width:1200px;margin:24px auto;padding:0 16px}
.cards,.forms{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.card,section{background:white;padding:18px;border-radius:12px;margin-bottom:16px}
input{padding:10px;width:100%;margin:6px 0 12px;border:1px solid #ccd5e2;border-radius:7px}
button,.btn{background:#2459a6;color:white;padding:10px 14px;border:0;border-radius:7px;text-decoration:none;cursor:pointer;display:inline-block}
small,.muted{color:#667791}.notice{background:#fff3d8;padding:12px;border-radius:8px;margin:12px 0}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:9px;border-bottom:1px solid #e4e9f0;vertical-align:top}
pre{white-space:pre-wrap;max-width:420px}.pill{background:#e4f5e9;padding:4px 8px;border-radius:14px}
</style></head><body><header><h1>🤖 Robô de Cobranças — COMBO UBS / SISLOGA</h1>
Preparação automática • WhatsApp Web • Envio manual</header><main>
{% for m in get_flashed_messages() %}<div class="notice">{{m}}</div>{% endfor %}
<div class="cards"><div class="card"><h2>{{stats.nfs}}</h2>Notas fiscais</div><div class="card"><h2>{{stats.contacts}}</h2>Contatos</div>
<div class="card"><h2>{{stats.blocked}}</h2>NFs bloqueadas</div><div class="card"><h2>{{stats.pending}}</h2>Mensagens prontas</div></div>
<div class="notice"><b>Importante:</b> só entram na fila contatos com telefone validado e autorização registrada. Nenhuma mensagem é enviada pelo sistema.</div>
<div class="forms">
<section><h2>Importar contatos CSV</h2><p class="muted">Colunas: municipio,uf,servidor,telefone,telefone_validado,autorizado. Use 1 para validar/autorizar.</p>
<form action="/import/contacts" method="post" enctype="multipart/form-data"><input type="file" name="file" accept=".csv" required><button>Importar contatos</button></form></section>
<section><h2>Importar notas CSV</h2><p class="muted">Colunas: nf,municipio,uf,transportadora,status,previsao_entrega,data_entrega,bloqueada. Datas AAAA-MM-DD.</p>
<form action="/import/invoices" method="post" enctype="multipart/form-data"><input type="file" name="file" accept=".csv" required><button>Importar notas</button></form></section></div>
<section><h2>Rastreamento — Translovato</h2>
<p>A Translovato disponibiliza consulta pública por <b>CNPJ/CPF e número da Nota Fiscal</b>. A página pública consultada não documenta uma API de consulta automática; por isso, o MVP abre o portal oficial e permite registrar manualmente o resultado.</p>
<p><b>CNPJ do remetente configurado:</b> {{translovato_cnpj}}</p>
<a class="btn" href="{{tracking_url}}" target="_blank" rel="noopener">Abrir portal oficial Minha Carga</a>
<p class="muted">No portal, informe o CNPJ/CPF e a NF. Depois, registre abaixo o status, a última ocorrência e a previsão retornados. Não informe senha do portal no robô.</p>
</section>
<section><h2>Notas fiscais e acompanhamento</h2>
{% if invoices %}<table><tr><th>NF / Município</th><th>Transportadora / Status</th><th>Última consulta</th><th>Registrar resultado</th></tr>
{% for i in invoices %}<tr><td><b>{{i.nf}}</b><br>{{i.municipality}}/{{i.state}}</td>
<td>{{i.carrier or 'Não informada'}}<br>{{i.status}}</td>
<td>{% if i.checked_at %}{{i.checked_at}}<br><b>{{i.tracking_status}}</b><br>{{i.last_event}}<br>Previsão: {{i.tracking_eta or 'Não informada'}}{% else %}<span class="muted">Ainda não consultada</span>{% endif %}</td>
<td><form method="post" action="/tracking/{{i.id}}">
<label>Status</label><select name="status" required><option value="faturada">Faturada</option><option value="em_transporte">Em transporte</option><option value="entregue">Entregue</option><option value="ocorrencia">Ocorrência / pendência</option></select>
<label>Última ocorrência / localização</label><input name="last_event" maxlength="500" placeholder="Ex.: carga em transferência na unidade..." required>
<label>Previsão de entrega</label><input type="date" name="eta">
<label>Data efetiva da entrega (se entregue)</label><input type="date" name="delivered_date">
<button>Salvar consulta</button></form></td></tr>{% endfor %}</table>
{% else %}<p class="muted">Nenhuma NF importada.</p>{% endif %}
</section>
<section><h2>Contato logístico AgSUS</h2>
<p>Para divergências de entrega/recebimento do COMBO UBS, canais oficiais divulgados pelo Ministério da Saúde:</p>
<ul><li>logistica.comboubs@agenciasus.org.br</li><li>contatocombos@agenciasus.org.br</li><li>(61) 99660-2116 · (61) 99657-7811 · (61) 99675-4516 · (61) 99658-9289</li></ul>
</section>
<section><h2>Preparar mensagens elegíveis</h2><p class="muted">Faturadas/em transporte exigem previsão. Entregues exigem data efetiva. NFs bloqueadas e contatos sem validação/autorização são ignorados.</p>
<form action="/prepare" method="post"><button>Preparar fila automaticamente</button></form></section>
<section><h2>Fila de mensagens</h2>{% if rows %}<table><tr><th>NF / município</th><th>Contato</th><th>Etapa</th><th>Texto</th><th>Ação</th></tr>
{% for r in rows %}<tr><td><b>{{r.nf}}</b><br>{{r.municipality}}/{{r.state}}</td><td>{{r.server_name}}<br>{{r.phone}}</td>
<td>{{r.stage}}<br>Modelo {{r.template_number}}</td><td><details><summary>Visualizar</summary><pre>{{r.body}}</pre></details></td><td>
<a class="btn" target="_blank" rel="noopener" href="https://wa.me/{{r.phone_digits}}?text={{r.encoded}}">Abrir WhatsApp</a>
{% if not r.sent_confirmed_at %}<form method="post" action="/confirm/{{r.id}}" onsubmit="return confirm('Você clicou em Enviar no WhatsApp?')"><button>Registrar envio</button></form>{% else %}<span class="pill">Envio registrado</span>{% endif %}</td></tr>{% endfor %}</table>
{% else %}<p class="muted">Fila vazia. Importe dados e prepare mensagens.</p>{% endif %}</section>
</main></body></html>
"""

@app.get("/")
def home():
    with connect() as c:
        stats=dict(nfs=c.execute("SELECT COUNT(*) FROM invoices").fetchone()[0],
                   contacts=c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
                   blocked=c.execute("SELECT COUNT(*) FROM invoices WHERE blocked=1").fetchone()[0],
                   pending=c.execute("SELECT COUNT(*) FROM queue WHERE sent_confirmed_at=''").fetchone()[0])
        rows=c.execute("""SELECT q.*,i.nf,i.municipality,i.state,ct.server_name,ct.phone
          FROM queue q JOIN invoices i ON i.id=q.invoice_id JOIN contacts ct ON ct.id=q.contact_id
          ORDER BY q.id DESC LIMIT 150""").fetchall()
        invoices=c.execute("""SELECT i.*, th.checked_at, th.status AS tracking_status,
          th.last_event, th.eta AS tracking_eta FROM invoices i
          LEFT JOIN tracking_history th ON th.id=(SELECT MAX(id) FROM tracking_history WHERE invoice_id=i.id)
          ORDER BY i.id DESC LIMIT 200""").fetchall()
        data=[]
        for x in rows:
            d=dict(x);d["phone_digits"]=digits(d["phone"]);d["encoded"]=quote(d["body"]);data.append(d)
    return render_template_string(HTML,stats=stats,rows=data,invoices=invoices,tracking_url=TRANSLOVATO_TRACKING_URL,translovato_cnpj=TRANSLOVATO_CNPJ)

@app.post("/import/contacts")
def import_contacts():
    count=0
    for r in read_csv(request.files["file"]):
        p=digits(r.get("telefone",""))
        if not (r.get("municipio") and r.get("uf") and r.get("servidor") and valid_phone(p)): continue
        with connect() as c:
            c.execute("""INSERT OR IGNORE INTO contacts(municipality,state,server_name,phone,phone_validated,authorized)
                VALUES(?,?,?,?,?,?)""",(r["municipio"].strip(),r["uf"].strip().upper(),r["servidor"].strip(),p,
                int(r.get("telefone_validado","0")=="1"),int(r.get("autorizado","0")=="1")))
            count+=c.execute("SELECT changes()").fetchone()[0]
    flash(f"{count} contatos importados. Contatos não validados/autorizados não entram na fila.")
    return redirect(url_for("home"))

@app.post("/import/invoices")
def import_invoices():
    count=0
    with connect() as c:
        for r in read_csv(request.files["file"]):
            if not (r.get("nf") and r.get("municipio") and r.get("uf")): continue
            status=(r.get("status") or "faturada").strip().lower()
            if status not in ("faturada","em_transporte","entregue"): status="faturada"
            c.execute("""INSERT INTO invoices(nf,municipality,state,carrier,status,expected_delivery,delivered_date,blocked)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(nf) DO UPDATE SET municipality=excluded.municipality,
              state=excluded.state,carrier=excluded.carrier,status=excluded.status,expected_delivery=excluded.expected_delivery,
              delivered_date=excluded.delivered_date,blocked=MAX(invoices.blocked,excluded.blocked)""",
              (r["nf"].strip(),r["municipio"].strip(),r["uf"].strip().upper(),r.get("transportadora","").strip(),
               status,r.get("previsao_entrega","").strip(),r.get("data_entrega","").strip(),int(r.get("bloqueada","0")=="1")))
            count+=1
    flash(f"{count} linhas de notas processadas; bloqueios existentes foram preservados.")
    return redirect(url_for("home"))

@app.post("/tracking/<int:invoice_id>")
def save_tracking(invoice_id):
    status=(request.form.get("status") or "").strip().lower()
    allowed={"faturada","em_transporte","entregue","ocorrencia"}
    if status not in allowed:
        flash("Status de rastreamento inválido.")
        return redirect(url_for("home"))
    last_event=(request.form.get("last_event") or "").strip()
    eta=(request.form.get("eta") or "").strip()
    delivered=(request.form.get("delivered_date") or "").strip()
    if not last_event:
        flash("Informe a última ocorrência exibida no portal da Translovato.")
        return redirect(url_for("home"))
    if status=="entregue" and not delivered:
        flash("Para marcar como entregue, informe a data efetiva da entrega.")
        return redirect(url_for("home"))
    for value in (eta,delivered):
        if value:
            try: date.fromisoformat(value)
            except ValueError:
                flash("Data inválida.")
                return redirect(url_for("home"))
    with connect() as c:
        if not c.execute("SELECT id FROM invoices WHERE id=?",(invoice_id,)).fetchone():
            flash("Nota fiscal não encontrada.")
            return redirect(url_for("home"))
        c.execute("""INSERT INTO tracking_history(invoice_id,checked_at,status,last_event,eta,source)
          VALUES(?,?,?,?,?,'manual-portal-translovato')""",
          (invoice_id,datetime.now().isoformat(timespec="seconds"),status,last_event,eta))
        if status in ("faturada","em_transporte"):
            c.execute("UPDATE invoices SET status=?,expected_delivery=? WHERE id=?",(status,eta,invoice_id))
        elif status=="entregue":
            c.execute("UPDATE invoices SET status='entregue',delivered_date=?,expected_delivery=? WHERE id=?",(delivered,eta,invoice_id))
        else:
            c.execute("UPDATE invoices SET status='em_transporte' WHERE id=?",(invoice_id,))
    flash("Consulta registrada manualmente a partir do portal da Translovato.")
    return redirect(url_for("home"))


@app.post("/prepare")
def prepare_route():
    flash(f"{prepare()} mensagens preparadas. Nenhuma foi enviada.")
    return redirect(url_for("home"))

@app.post("/confirm/<int:qid>")
def confirm(qid):
    with connect() as c:
        c.execute("UPDATE queue SET sent_confirmed_at=? WHERE id=?",
                  (datetime.now().isoformat(timespec="seconds"),qid))
    flash("Registro manual salvo. O sistema não consegue verificar o envio dentro do WhatsApp.")
    return redirect(url_for("home"))

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=8765,debug=False)
