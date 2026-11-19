#!/usr/bin/env python3
"""Painel local de cobranças, sem envio automático e sem rastreamento automatizado."""
import csv, html, io, os, re, secrets, sqlite3, urllib.parse, webbrowser
from datetime import datetime, date, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import RLock
ROOT=Path(__file__).parent
DB=ROOT/'robo_cobrancas.db'
TOKEN=secrets.token_urlsafe(32)
LOCK=RLock()
PORT=int(os.getenv('PORT','8765'))
CARRIERS={'TRANSLOVATO':'https://www.translovato.com.br/minha-carga','JAMEF':'https://www.jamef.com.br/','CARVALIMA':'https://cliente.carvalima.com.br/rastreamento'}
STATUSES=['Não consultado','Em trânsito','Saiu para entrega','Entregue','Ocorrência','Não localizado','Erro na consulta']
def esc(v):return html.escape('' if v is None else str(v),quote=True)
def now():return datetime.now().isoformat(timespec='seconds')
def conn():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def parse_date(x):
 if not x:return None
 try:return date.fromisoformat(x)
 except ValueError:raise ValueError('Data inválida. Use o seletor de data.')
def deadline(x):return (parse_date(x)+timedelta(days=3)).strftime('%d/%m/%Y') if x else 'a confirmar'
def local_date(x):return parse_date(x).strftime('%d/%m/%Y') if x else 'Não informado'
def phone_norm(s):
 p=re.sub(r'\D','',s)
 if len(p) in (10,11):p='55'+p
 if not(p.startswith('55') and len(p) in (12,13)):raise ValueError('Telefone inválido.')
 return p
def csrf():return f'<input type="hidden" name="csrf" value="{TOKEN}">'
def form(path,inner,button):return f'<form action="{path}" method="post" class="inline">{csrf()}{inner}<button>{esc(button)}</button></form>'
def inp(n,v='',typ='text'):return f'<input name="{n}" type="{typ}" value="{esc(v)}">'
def sel(n,options,current):return f'<select name="{n}">'+''.join(f'<option value="{esc(o)}" {"selected" if o==current else ""}>{esc(o)}</option>' for o in options)+'</select>'
def audit(c,act,ref,txt):c.execute('INSERT INTO audit(at,action,reference,description) VALUES(?,?,?,?)',(now(),act,ref,txt))

def importar_ctes():
 """Importa CT-es AGSUS sem alterar rastreamento, entrega, bloqueios ou mensagens."""
 f=ROOT/'ctes_agsus.csv'
 if not f.exists():return 0
 imported=0
 with LOCK,conn() as c:
  existing={r['name'] for r in c.execute('PRAGMA table_info(invoices)')}
  columns={'cte_number':'TEXT','cte_eta':'TEXT','cte_issued':'TEXT','cte_source':'TEXT','cte_destination':'TEXT'}
  for name,typ in columns.items():
   if name not in existing:c.execute(f'ALTER TABLE invoices ADD COLUMN {name} {typ}')
  with f.open(encoding='utf-8-sig',newline='') as h:
   for r in csv.DictReader(h):
    if r['Cliente'].strip().upper()!='AGÊNCIA BRASILEIRA DE APOIO À GESTÃO DO SUS':continue
    if r['Transportadora'].strip().upper()!='TRANSLOVATO':continue
    nf=r['NF'].strip();ct=r['CTe'].strip();raw=r['Previsão de entrega (CT-e)'].strip()
    try:eta=datetime.strptime(raw,'%d/%m/%Y').date().isoformat()
    except ValueError:continue
    inv=c.execute('SELECT carrier,cte_number FROM invoices WHERE nf=?',(nf,)).fetchone()
    if not inv or (inv['carrier'] or '').upper()!='TRANSLOVATO':continue
    if inv['cte_number'] and inv['cte_number']!=ct:continue
    c.execute("UPDATE invoices SET cte_number=?,cte_eta=?,cte_issued=?,cte_source=?,cte_destination=? WHERE nf=? AND (cte_number IS NULL OR cte_number=?)",
      (ct,eta,r['Emissão CT-e'],r['Arquivo PDF'],r['Município (texto CT-e)'],nf,ct))
    if not inv['cte_number']:imported+=1
  if imported:audit(c,'Importação documental AGSUS','CTES',f'{imported} CT-es vinculados. Previsões documentais não equivalem a rastreamento confirmado. Nenhuma mensagem enviada.')
 return imported

def message_template(stage,num,r):
 mu=f"{r['municipality'].title()}/{r['uf']}";nf=r['nf'];carrier=r['carrier'];eta=local_date(r['predicted_at']);dt=local_date(r['delivery_at']);lim=deadline(r['delivery_at'])
 if stage=='transito' and not r['predicted_at']:raise ValueError('Informe uma previsão de entrega confirmada antes de preparar o aviso de transporte.')
 if stage=='entregue' and (r['tracking_status']!='Entregue' or not r['delivery_at']):raise ValueError('Confirme o status Entregue e a data efetiva da entrega.')
 head=f"📍 Município: {mu}\n📄 Nota Fiscal: {nf}\n🚛 Transportadora: {carrier}\n"
 if stage=='transito':
  variants=[
   f"Olá, tudo bem por aí? 😊\n\nPassando para compartilhar uma ótima notícia!\n\n🚚 Mais uma remessa de equipamentos do COMBO UBS – Ministério da Saúde já foi faturada e está a caminho do seu município!\n\n{head}📅 Previsão de entrega: {eta}\n\n⚠️ Um aviso muito importante!\n\nAssim que os equipamentos forem recebidos, solicitamos uma foto da Nota Fiscal assinada, contendo nome completo legível e CPF do servidor responsável. Precisamos desse documento para atualizar o SISLOGA e concluir o registro oficial do recebimento.\n\nQualquer dúvida, estamos à disposição! 💙",
   f"Bom dia! Tudo bem?\n\nInformamos que os equipamentos do COMBO UBS – Ministério da Saúde já foram faturados e encaminhados à transportadora.\n\n{head}📅 Previsão de entrega: {eta}\n\n📌 Após a chegada dos equipamentos, solicitamos que a Nota Fiscal seja assinada pelo servidor responsável, com nome completo legível e CPF. Envie uma foto do documento por este WhatsApp para atualização no SISLOGA.\n\nAgradecemos a colaboração!",
   f"Olá! Temos novidades sobre os equipamentos do seu município! 📦✨\n\nA remessa do COMBO UBS – Ministério da Saúde foi faturada e já está com a transportadora!\n\n{head}🗓️ Chegada prevista: {eta}\n\n✅ Quando os equipamentos chegarem, confira a entrega e peça a assinatura da NF pelo servidor responsável, com nome completo legível e CPF. Depois, envie uma foto da NF assinada por aqui para registrarmos a entrega no SISLOGA.\n\nContamos com sua colaboração! 💙",
   f"Olá, tudo bem? 👋\n\n🚚 Os equipamentos do COMBO UBS – Ministério da Saúde já foram faturados e estão em transporte.\n\n{head}📅 Previsão de entrega: {eta}\n\n⚠️ Após o recebimento, precisamos de uma foto da NF assinada pelo servidor responsável, com nome completo legível e CPF, para atualização no SISLOGA.\n\nAgradecemos a colaboração!"
  ]
 else:
  variants=[
   f"Olá, tudo bem por aí? 😊\n\n✅ A transportadora informou que os equipamentos do COMBO UBS – Ministério da Saúde foram entregues!\n\n{head}📦 Data da entrega: {dt}\n\n⚠️ Precisamos da Nota Fiscal assinada, com nome completo legível e CPF do servidor responsável.\n\n⏰ O prazo máximo para envio é de 3 dias corridos contados da entrega dos equipamentos.\n📅 Data-limite: {lim}\n\nEnvie uma foto legível por este WhatsApp para atualizarmos o SISLOGA. Muito obrigada! 💙",
   f"Prezado(a), bom dia!\n\nConforme atualização da transportadora, os equipamentos do COMBO UBS – Ministério da Saúde foram entregues.\n\n{head}📅 Data da entrega: {dt}\n\nSolicitamos a NF devidamente assinada pelo servidor responsável, com nome completo legível e CPF, para registrar o recebimento no SISLOGA.\n\n⚠️ Prazo máximo: 3 dias corridos contados da data da entrega.\n📅 Prazo final: {lim}\n\nAgradecemos a atenção e a colaboração.",
   f"Olá! Tudo bem? 💙\n\n📦 Os equipamentos chegaram! A transportadora confirmou a entrega da remessa do COMBO UBS – Ministério da Saúde.\n\n{head}✅ Entrega registrada em: {dt}\n\nPor favor, envie aqui uma foto da NF assinada, com nome completo legível e CPF do servidor responsável.\n\n⏳ O documento deve ser enviado em até 3 dias corridos contados da entrega dos equipamentos.\n📅 Data-limite: {lim}\n\nAssim conseguiremos atualizar o SISLOGA. Agradecemos pela parceria!",
   f"Olá, bom dia!\n\n✅ Consta no rastreamento da transportadora que os equipamentos do COMBO UBS – Ministério da Saúde foram entregues.\n\n{head}📅 Data da entrega: {dt}\n\n⚠️ PENDÊNCIA: ENVIO DA NOTA FISCAL ASSINADA\n\nSolicitamos a NF assinada pelo servidor responsável, com nome completo legível e CPF, para confirmação no SISLOGA.\n\n⏰ Prazo máximo: 3 dias corridos a contar da entrega.\n📅 Data-limite: {lim}\n\nCaso já tenha enviado a documentação, por favor nos informe para conferência. Obrigado!"
  ]
 return variants[num-1]

def preparar_mensagens_elegiveis():
 # Prepara mensagens sem envio
 total=0
 with LOCK,conn() as c:
  notas=c.execute("SELECT * FROM invoices WHERE blocked=0 AND invoice_signed=0 AND tracking_status IN ('Em trânsito','Saiu para entrega','Entregue') ORDER BY CAST(nf AS INTEGER)").fetchall()
  for r in notas:
   etapa='entregue' if r['tracking_status']=='Entregue' else 'transito'
   if etapa=='entregue' and not r['delivery_at']:continue
   if etapa=='transito' and not r['predicted_at']:continue
   if c.execute("SELECT 1 FROM messages WHERE nf=? AND stage=? AND status IN ('Rascunho','Aprovada','Enviada')",(r['nf'],etapa)).fetchone():continue
   contato=c.execute('''SELECT ct.* FROM contacts ct JOIN contact_invoices ci ON ci.contact_id=ct.id
    WHERE ci.nf=? AND ct.phone_validated=1 AND ct.consent=1 ORDER BY ct.id LIMIT 1''',(r['nf'],)).fetchone()
   if not contato:continue
   modelo=c.execute('SELECT next_number FROM counters WHERE stage=?',(etapa,)).fetchone()['next_number']
   corpo=message_template(etapa,modelo,r)
   c.execute('''INSERT INTO messages(nf,contact_id,stage,template_no,body,status,created_at,approved_at)
    VALUES(?,?,?,?,?,'Rascunho',?,NULL)''',(r['nf'],contato['id'],etapa,modelo,corpo,now(),now()))
   c.execute('UPDATE counters SET next_number=? WHERE stage=?',(1 if modelo==4 else modelo+1,etapa))
   audit(c,'Rascunho preparado automaticamente',r['nf'],f'Etapa {etapa}; modelo {modelo}; aguardando aprovação manual; não enviado')
   total+=1
 return total

def gerar_vcard():
 # Exportação voluntária de contatos conferidos
 def vcard_escape(s):
  return str(s or '').replace('\\','\\\\').replace(';','\\;').replace(',','\\,').replace('\n','\\n')
 with conn() as c:
  contatos=c.execute('SELECT display_name,phone FROM contacts WHERE phone_validated=1 AND consent=1 ORDER BY display_name').fetchall()
 seen=set(); cards=[]
 for ct in contatos:
  phone=ct['phone']
  if phone in seen:continue
  seen.add(phone)
  cards.append('BEGIN:VCARD\nVERSION:3.0\nFN:'+vcard_escape(ct['display_name'])+'\nTEL;TYPE=CELL:+'+phone+'\nNOTE:Contato do controle COMBO UBS. Etiqueta Cobrança deve ser aplicada manualmente no WhatsApp Business.\nEND:VCARD')
 return ('\n'.join(cards)+'\n').encode('utf-8') if cards else b''

CSS='''body{font:14px system-ui,Arial;background:#f4f6fb;color:#203047;margin:0}header{background:#1e3356;color:white;padding:23px 4vw}h1{margin:0;font-size:22px}main{max-width:1400px;margin:20px auto;padding:0 17px}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.card,.panel{background:white;border:1px solid #e2e7ee;border-radius:12px;padding:16px;margin-bottom:14px}.card b{font-size:26px}.card small{display:block;color:#64748b}.panel h2{margin-top:0;font-size:18px}.warn{background:#fff7e7;padding:12px;border-radius:8px}.ok{color:#08744c}.muted{color:#64748b;font-size:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e5eaf0;padding:8px;vertical-align:top}th{font-size:12px;color:#52647b}.scroller{overflow:auto;max-height:640px}input,select,textarea{font:inherit;padding:6px;border:1px solid #cdd5e1;border-radius:5px;max-width:180px}textarea{min-width:280px;min-height:85px}button,.link{background:#2656a7;color:white;border:0;border-radius:5px;padding:8px 10px;cursor:pointer;text-decoration:none;display:inline-block;font-size:12px}button:disabled{background:#a7aeb8}.inline{display:inline-flex;align-items:center;gap:4px;flex-wrap:wrap;margin:3px 0}details{margin:5px 0}summary{cursor:pointer;color:#1b559c}.pill{border-radius:12px;background:#e9eef5;padding:3px 7px;font-size:11px;display:inline-block}.block{background:#ffebdc;color:#934418}.info{padding:10px;background:#e9f2fc;border-radius:6px}.flex{display:flex;gap:10px;flex-wrap:wrap}.flex>*{flex:1}.message{white-space:pre-wrap;max-width:700px;font:13px system-ui}.actions{min-width:180px}a{color:#2156a0}.footer{font-size:12px;color:#61728a;margin:25px 0}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}}'''
def dashboard(msg=''):
 # A abertura do painel não deve importar dados nem criar/aprovar mensagens.
 with conn() as c:
  inv=c.execute('''SELECT i.*, (SELECT COUNT(*) FROM contact_invoices ci WHERE ci.nf=i.nf) contacts_count FROM invoices i ORDER BY CAST(i.nf AS INTEGER)''').fetchall()
  contacts=c.execute('SELECT * FROM contacts ORDER BY municipality,server_name').fetchall()
  messages=c.execute('''SELECT m.*,i.municipality,i.uf,i.blocked,i.invoice_signed,i.nf,c.display_name,c.phone,c.consent,c.phone_validated FROM messages m JOIN invoices i ON i.nf=m.nf JOIN contacts c ON c.id=m.contact_id ORDER BY m.id DESC LIMIT 200''').fetchall()
  counts={'total':len(inv),'blocked':sum(r['blocked'] for r in inv),'ready':sum(r['tracking_status']=='Entregue' and not r['blocked'] and not r['invoice_signed'] for r in inv),'contacts':len(contacts),'ctes':sum(bool(r['cte_number']) for r in inv)}
 cards=''.join(f'<div class="card"><b>{v}</b><small>{k}</small></div>' for k,v in [('Notas cadastradas',counts['total']),('Bloqueadas (não cobrar)',counts['blocked']),('Entregues a avaliar',counts['ready']),('Contatos únicos (não validados)',counts['contacts']),('CT-es AGSUS vinculados',counts['ctes'])])
 invrows=''
 for i in inv:
  nf=i['nf']; blocked=bool(i['blocked']); car=i['carrier'];url=CARRIERS.get((car or '').upper()); state=i['tracking_status']
  opts=sel('status',STATUSES,state)
  fields=f'<input type="hidden" name="nf" value="{esc(nf)}">{opts} <label>Data da entrega {inp("delivery_at",i["delivery_at"] or "","date")}</label><label>Previsão {inp("predicted_at",i["predicted_at"] or "","date")}</label><label>Último registro {inp("last_movement",i["last_movement"] or "")}</label><label>Localização {inp("location",i["location"] or "")}</label><label>Histórico {inp("tracking_history",i["tracking_history"] or "")}</label>'
  mark=form('/update',fields,'Salvar rastreamento')
  signed=form('/signed',f'<input type="hidden" name="nf" value="{esc(nf)}">','Confirmar NF assinada recebida') if not i['invoice_signed'] and not blocked else ''
  bl=f'<span class="pill block" title="{esc(i["block_reason"])}">NÃO COBRAR</span>' if blocked else ''
  toggled='' if blocked else form('/block',f'<input type="hidden" name="nf" value="{esc(nf)}">','Bloquear cobrança')
  stageactions=''
  if not blocked and not i['invoice_signed']:
   stageactions=form('/draft',f'<input type="hidden" name="nf" value="{esc(nf)}"><input type="hidden" name="stage" value="transito">','Preparar aviso') if i['predicted_at'] and state in ('Em trânsito','Saiu para entrega') else ''
   stageactions+=form('/draft',f'<input type="hidden" name="nf" value="{esc(nf)}"><input type="hidden" name="stage" value="entregue">','Preparar cobrança') if state=='Entregue' and i['delivery_at'] else ''
  invrows+=f'<tr><td><b>{esc(nf)}</b><small class="muted">{esc(i["municipality"].title())}/{esc(i["uf"])}</small><br>{bl}</td><td>{esc(car)}<br>{f"<a href=\"{url}\" target=\"_blank\" rel=\"noopener\">Abrir rastreamento</a>" if url else "<span class=muted>Transportadora a confirmar</span>"}</td><td>{esc(state)}<br><small>{esc(i["contacts_count"])} contatos · Prazo: {deadline(i["delivery_at"])}</small><br><small class="muted">{f'CT-e {esc(i["cte_number"])} · previsão no documento: {local_date(i["cte_eta"])} (não verificada no portal)' if i['cte_number'] else 'CT-e não vinculado'}</small></td><td><details><summary>Atualizar consulta</summary>{mark}</details>{signed}{toggled}</td><td>{stageactions}</td></tr>'
 queue=''
 for m in messages:
  mid=m['id'];ok=m['consent'] and m['phone_validated'] and not m['blocked'] and not m['invoice_signed']
  actions=''
  if m['status']=='Rascunho':
   actions=form('/approve',f'<input type="hidden" name="id" value="{mid}">','Aprovar para WhatsApp') if ok else '<span class="pill block">Validar telefone e consentimento</span>'
  if m['status']=='Aprovada':
   wa='https://wa.me/'+m['phone']+'?'+urllib.parse.urlencode({'text':m['body']})
   actions=f'<a class="link" href="{esc(wa)}" target="_blank" rel="noopener noreferrer" style="background:#128c64">Abrir WhatsApp com texto pronto</a> <a href="/revisar?id={mid}">Ver detalhes / registrar envio</a>' if ok else '<span class="pill block">Contato exige conferência</span>'
  queue+=f'<tr><td>NF {esc(m["nf"])}<br><small>{esc(m["display_name"])}</small></td><td>{"Transporte" if m["stage"]=="transito" else "Entrega"} · Modelo {m["template_no"]}<br><span class="pill">{esc(m["status"])}</span></td><td><details><summary>Ver mensagem</summary><pre class="message">{esc(m["body"])}</pre></details></td><td>{actions}</td></tr>'
 contactrows=''
 for ct in contacts:
  changes=f'<input type="hidden" name="id" value="{ct["id"]}"><label><input type="checkbox" name="validated" value="1" {"checked" if ct["phone_validated"] else ""}> Telefone confirmado</label><label><input type="checkbox" name="consent" value="1" {"checked" if ct["consent"] else ""}> Autorização registrada</label>'
  contactrows+=f'<tr><td>{esc(ct["display_name"])}</td><td>+{esc(ct["phone"])}</td><td>{esc(ct["label"])}</td><td>{form("/contact",changes,"Salvar")}</td></tr>'
  
 text=f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Robô de Cobranças COMBO UBS</title><style>{CSS}</style></head><body><header><h1>📦 Robô de Cobranças • COMBO UBS / SISLOGA</h1><small>Textos preparados automaticamente após validação · Envio somente ao clicar no WhatsApp</small></header><main>{f'<p class="info">{esc(msg)}</p>' if msg else ''}<section class="cards">{cards}</section><div class="warn"><b>ATENÇÃO:</b> CT-es da AGSUS importados como dados documentais; transportadoras ainda não foram consultadas automaticamente. Previsão do CT-e não significa rastreamento confirmado. Os contatos foram extraídos das notas e precisam de confirmação de telefone e autorização antes do contato. Etiqueta “Cobrança” é interna, não sincroniza com a etiqueta do aplicativo WhatsApp Business.</div><section class="panel"><h2>Notas fiscais e rastreamento</h2><p class="muted">CT-es e previsões documentadas aparecem abaixo; abra o portal para conferir antes de registrar a previsão efetiva e o status. Todas as NFs bloqueadas estão protegidas contra geração de mensagens.</p><div class="scroller"><table><tr><th>Nota / destino</th><th>Transportadora</th><th>Rastreio / Prazo</th><th>Registro manual</th><th>Preparação</th></tr>{invrows}</table></div></section><section class="panel"><h2>WhatsApp Business Web — mensagens prontas e contatos</h2><p><b>Contatos:</b> depois de conferir o telefone e registrar autorização, <a href="/contatos.vcf">baixe os contatos validados em VCF</a> e importe na agenda Google/telefone. A importação NÃO ocorre sozinha. O WhatsApp poderá reconhecer os contatos quando a agenda sincronizar. Etiquetas nativas são manuais.</p><p><b>Integração assistida:</b> não existe sincronização de sessão com o robô. Para usar o número comercial, abra <a href="https://web.whatsapp.com/" target="_blank" rel="noopener noreferrer">WhatsApp Web (abrir / conferir conexão)</a> no mesmo navegador. Se aparecer QR Code, no aplicativo WhatsApp Business do telefone use <b>Dispositivos conectados → Conectar dispositivo</b>. Se aparecer sua lista de conversas, a sessão Web está disponível.</p><p><b>Teste seguro:</b> não é necessário enviar mensagem de teste. Após confirmar telefone e autorização, o sistema prepara sozinho as mensagens das NFs elegíveis. Na fila, clique em <b>Abrir WhatsApp com texto pronto</b> e envie manualmente. O botão de confirmação no robô deve ser marcado apenas após o envio real.</p><p class="muted">A sessão do WhatsApp Web pertence ao navegador, não ao aplicativo local. Este robô não verifica automaticamente se sua conta WhatsApp está conectada e não acessa conversas, contatos nativos ou etiquetas do aplicativo.</p></section><section class="panel"><h2>Mensagens prontas para abrir no WhatsApp</h2><p class="muted">Fluxo: validar telefone e autorização uma vez → registrar rastreamento comprovado → texto gerado automaticamente → abrir WhatsApp Web → clicar Enviar. O robô não pode detectar o clique de envio: registre o envio manualmente no link de detalhes, quando possível.</p><div class="scroller"><table><tr><th>Destinatário</th><th>Etapa</th><th>Texto</th><th>Ações</th></tr>{queue or '<tr><td colspan="4">Nenhuma mensagem preparada.</td></tr>'}</table></div></section><section class="panel"><h2>Contatos • etiqueta interna Cobrança</h2><p class="muted">Padrão: Pm Município/UF - SERVIDOR. Os registros abaixo NÃO foram criados na agenda do aparelho.</p><div class="scroller"><table><tr><th>Contato</th><th>Telefone</th><th>Etiqueta</th><th>Conferência</th></tr>{contactrows}</table></div></section><p class="footer">Aplicação local em 127.0.0.1. Sem login e sem acesso externo: não publique na internet. Faça backups seguros do arquivo SQLite e proteja os dados pessoais sob LGPD.</p></main></body></html>'''
 return text.encode('utf-8')
def tela_revisao(mid):
 """Tela exclusiva para revisar a mensagem aprovada e abrir o WhatsApp Web."""
 with conn() as c:
  m=c.execute("""SELECT m.*,ct.display_name,ct.phone,ct.phone_validated,ct.consent,
     i.municipality,i.uf,i.blocked,i.invoice_signed,i.tracking_status,i.delivery_at
     FROM messages m JOIN contacts ct ON ct.id=m.contact_id
     JOIN invoices i ON i.nf=m.nf WHERE m.id=?""",(mid,)).fetchone()
 if not m:
  return None
 can_open=(m['status']=='Aprovada' and m['phone_validated'] and m['consent']
           and not m['blocked'] and not m['invoice_signed'])
 wa='https://wa.me/'+m['phone']+'?'+urllib.parse.urlencode({'text':m['body']})
 stage='Aviso de transporte' if m['stage']=='transito' else 'Confirmação de entrega'
 message=esc(m['body'])
 if can_open:
  controls=(f'<p><a class="link" href="{esc(wa)}" target="_blank" rel="noopener noreferrer" '
   'style="background:#128c64;font-size:16px;padding:13px 18px">1. Abrir conversa com texto preenchido</a></p>'
   '<p class="muted">Confira o número e o texto no WhatsApp Web e clique em Enviar manualmente. '
   'Esta ação apenas abre a conversa; nenhum envio acontece pelo robô.</p>'
   f'<form action="/confirm" method="post">{csrf()}<input type="hidden" name="id" value="{mid}">'
   '<p><label><input name="sent_checked" type="checkbox" value="1" required> '
   'Confirmo que eu mesma enviei esta mensagem no WhatsApp Business para o contato correto.</label></p>'
   '<button type="submit">2. Registrar no robô que enviei</button></form>')
 else:
  controls='<p class="warn">Esta mensagem não pode ser aberta para envio (pendente, cancelada, já registrada como enviada ou contato/NF sem validação). Volte ao painel.</p>'
 html_doc=f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Revisar WhatsApp • NF {esc(m['nf'])}</title><style>{CSS}</style></head><body>
 <header><h1>💬 Revisão antes do WhatsApp Business</h1><small>O robô não envia mensagens automaticamente.</small></header>
 <main style="max-width:850px"><p><a href="/">← Voltar ao painel</a></p><section class="panel">
 <h2>{esc(stage)} • NF {esc(m['nf'])}</h2>
 <p><b>Destinatário:</b> {esc(m['display_name'])}<br><b>Telefone:</b> +{esc(m['phone'])}<br><b>Município:</b> {esc(m['municipality'])}/{esc(m['uf'])}<br>
 <b>Modelo:</b> {m['template_no']} de 4 · <b>Situação:</b> {esc(m['status'])}</p>
 <p class="warn"><b>Antes de enviar:</b> confirme destinatário, NF, município, status real da entrega e data de previsão/prazo. O link pode abrir uma etapa intermediária do WhatsApp no navegador.</p>
 <h3>Texto que será preenchido</h3><pre class="message" style="white-space:pre-wrap;background:#f3f6fb;padding:16px;border-radius:9px;max-width:none;overflow-wrap:anywhere">{message}</pre>
 {controls}
 </section><p class="footer">A confirmação de envio é declarada pelo operador. Sem API, o robô não consegue comprovar automaticamente o envio ou a leitura no WhatsApp.</p></main></body></html>"""
 return html_doc.encode('utf-8')

class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  p=urllib.parse.urlparse(self.path)
  if p.path=='/revisar':
   try: mid=int(urllib.parse.parse_qs(p.query).get('id',[''])[0])
   except ValueError: self.send_error(400);return
   data=tela_revisao(mid)
   if data is None:self.send_error(404);return
  elif p.path=='/contatos.vcf':
   data=gerar_vcard()
   if not data:
    self.send_error(404,'Nenhum contato validado e autorizado para exportar');return
   self.send_response(200);self.send_header('Content-Type','text/vcard; charset=utf-8');self.send_header('Content-Disposition','attachment; filename="Contatos_Cobranca_COMBO_UBS.vcf"');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data);return
  elif p.path=='/':
   msg=urllib.parse.parse_qs(p.query).get('msg',[''])[0];data=dashboard(msg)
  else:self.send_error(404);return
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def do_POST(self):
  if self.path not in ['/update','/signed','/draft','/approve','/confirm','/contact','/block']:self.send_error(404);return
  try:
   n=int(self.headers.get('Content-Length','0'))
   if n>100000:self.send_error(413);return
   p=urllib.parse.parse_qs(self.rfile.read(n).decode('utf-8'),keep_blank_values=True)
   get=lambda k:p.get(k,[''])[0].strip()
   if not secrets.compare_digest(get('csrf'),TOKEN):self.send_error(403);return
   with LOCK,conn() as c:
    path=self.path;nf=get('nf');msg='Registro atualizado.'
    if path=='/update':
     state=get('status');delivery=get('delivery_at');eta=get('predicted_at')
     if state not in STATUSES:raise ValueError('Status desconhecido')
     if delivery:parse_date(delivery)
     if eta:parse_date(eta)
     if state=='Entregue' and not delivery:raise ValueError('Entrega confirmada requer data efetiva.')
     c.execute('UPDATE invoices SET tracking_status=?,delivery_at=?,predicted_at=?,last_movement=?,location=?,tracking_history=? WHERE nf=?',(state,delivery or None,eta or None,get('last_movement'),get('location'),get('tracking_history'),nf))
     audit(c,'Consulta registrada',nf,f'Status: {state}; entrega: {delivery}; previsão: {eta}. Registro manual.')
    elif path=='/block':
     c.execute('UPDATE invoices SET blocked=1,block_reason=? WHERE nf=?',('Bloqueado manualmente pelo operador',nf))
     c.execute("UPDATE messages SET status='Cancelada' WHERE nf=? AND status IN ('Rascunho','Aprovada')",(nf,))
     audit(c,'Bloqueio manual',nf,'Cobrança bloqueada')
    elif path=='/signed':
     c.execute('UPDATE invoices SET invoice_signed=1 WHERE nf=?',(nf,))
     c.execute("UPDATE messages SET status='Cancelada' WHERE nf=? AND status IN ('Rascunho','Aprovada')",(nf,))
     audit(c,'NF assinada',nf,'Recebimento confirmado manualmente')
    elif path=='/contact':
     cid=int(get('id'));validated=int(get('validated')=='1');consent=int(get('consent')=='1')
     c.execute('UPDATE contacts SET phone_validated=?,consent=? WHERE id=?',(validated,consent,cid))
     audit(c,'Contato validado',str(cid),f'Telefone validado {validated}; autorização registrada {consent}')
    elif path=='/draft':
     stage=get('stage');
     if stage not in ('transito','entregue'):raise ValueError('Etapa inválida')
     r=c.execute('SELECT * FROM invoices WHERE nf=?',(nf,)).fetchone()
     if r is None or r['blocked'] or r['invoice_signed']:raise ValueError('NF não encontrada, bloqueada ou já concluída')
     if stage=='transito' and r['tracking_status'] not in ('Em trânsito','Saiu para entrega'):raise ValueError('A carga ainda não está confirmada em transporte')
     if stage=='entregue' and r['tracking_status']!='Entregue':raise ValueError('Entrega não confirmada')
     recipient=c.execute('''SELECT ct.* FROM contacts ct JOIN contact_invoices ci ON ci.contact_id=ct.id WHERE ci.nf=? AND ct.phone_validated=1 AND ct.consent=1 ORDER BY ct.id LIMIT 1''',(nf,)).fetchone()
     if not recipient:raise ValueError('Nenhum contato relacionado, com telefone confirmado e autorização registrada')
     existing=c.execute("SELECT id FROM messages WHERE nf=? AND stage=? AND status IN ('Rascunho','Aprovada','Enviada')",(nf,stage)).fetchone()
     if existing:raise ValueError('Já existe mensagem dessa etapa para esta NF. Não foi criada duplicata.')
     model=c.execute('SELECT next_number FROM counters WHERE stage=?',(stage,)).fetchone()['next_number']
     body=message_template(stage,model,r)
     c.execute('INSERT INTO messages(nf,contact_id,stage,template_no,body,created_at) VALUES(?,?,?,?,?,?)',(nf,recipient['id'],stage,model,body,now()))
     c.execute('UPDATE counters SET next_number=? WHERE stage=?',(1 if model==4 else model+1,stage))
     audit(c,'Mensagem criada',nf,f'{stage} - modelo {model}; aguardando aprovação')
     msg=f'Mensagem {model} preparada para NF {nf}; nenhuma mensagem foi enviada.'
    elif path=='/approve':
     mid=int(get('id'));m=c.execute('''SELECT m.*,ct.phone_validated,ct.consent,i.blocked,i.invoice_signed FROM messages m JOIN contacts ct ON ct.id=m.contact_id JOIN invoices i ON i.nf=m.nf WHERE m.id=?''',(mid,)).fetchone()
     if not m or m['status']!='Rascunho' or not(m['phone_validated'] and m['consent']) or m['blocked'] or m['invoice_signed']:raise ValueError('Aprovação impedida: contato não validado/autorizado ou NF bloqueada/concluída')
     c.execute('UPDATE messages SET status=?,approved_at=? WHERE id=?',('Aprovada',now(),mid));audit(c,'Mensagem aprovada',m['nf'],f'Mensagem {mid} pronta para abrir WhatsApp')
    elif path=='/confirm':
     if get('sent_checked')!='1':raise ValueError('Marque a confirmação de que você enviou manualmente pelo WhatsApp.')
     mid=int(get('id'));m=c.execute('''SELECT m.*,i.blocked,i.invoice_signed FROM messages m JOIN invoices i ON i.nf=m.nf WHERE m.id=?''',(mid,)).fetchone()
     if not m or m['status']!='Aprovada' or m['blocked'] or m['invoice_signed']:raise ValueError('Envio não pode ser confirmado nesta condição.')
     c.execute('UPDATE messages SET status=?,confirmed_at=? WHERE id=?',('Enviada',now(),mid));audit(c,'Envio confirmado',m['nf'],'Operador declarou que enviou manualmente pelo WhatsApp; não é confirmação automática da plataforma')
   self.send_response(303);self.send_header('Location','/?msg='+urllib.parse.quote(msg));self.end_headers()
  except (ValueError,sqlite3.Error) as ex:
   self.send_response(303);self.send_header('Location','/?msg='+urllib.parse.quote('Erro: '+str(ex)));self.end_headers()
 def log_message(self,*args):pass
if __name__=='__main__':
 if not DB.exists():raise SystemExit('Banco inexistente: execute python build.py para criar a base inicial.')
 from importar_notas_novas import instalar
 added=instalar(DB,ROOT)
 print(f'Novas NFs AGSUS importadas: {added[0]}, contatos adicionados: {added[1]}, vínculos: {added[2]}.')
 n=importar_ctes()
 print(f'CT-es AGSUS novos vinculados: {n}. Dados anteriores preservados.')
 server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
 print(f'Abra http://127.0.0.1:{PORT} (Ctrl+C para parar)')
 try:server.serve_forever()
 except KeyboardInterrupt:pass
 finally:server.server_close()
